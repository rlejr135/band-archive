"""Real PG vote contention; only JWT principal resolution and signing are doubles.

The shared coordinator proves separate backends and pg_blocking_pids before
releasing the first Band lock. Target/vote rows, aggregates, audit, savepoints
and commits remain real. JWT verification is covered by companion HTTP tests.
"""

from dataclasses import dataclass
import json
import os

import pytest
from flask import request

from errors import register_error_handlers
from extensions import db
from models import AppUser, AuditEvent, BandMembership, Media, MediaVote, Song, SongSuggestion, SongSuggestionVote
import routes.content_writes as writes
import v2_vote_service as votes
from storage import storage
from tests.test_invite_link_concurrency import (  # noqa: F401
    _ordered_contention, invite_pg, private_pg17,
)


pytestmark = [pytest.mark.postgres_integration, pytest.mark.skipif(
    os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly provisioned owned localhost PG17 only')]


@dataclass(frozen=True)
class Principal:
    id: int
    supabase_user_id: str
    email: str
    display_name: str | None
    status: str
    email_verified: bool
    is_platform_admin: bool


def _fixture(invite_pg, monkeypatch, kind):
    app, engine, ids = invite_pg
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True)
    app.register_blueprint(writes.content_writes_bp)
    register_error_handlers(app)
    with app.app_context():
        principals = {}
        for label in ('owner', 'entrant'):
            user = db.session.get(AppUser, ids[label])
            principals[label] = Principal(**{name: getattr(user, name) for name in Principal.__dataclass_fields__})
        owner_membership = BandMembership.query.filter_by(
            band_id=ids['band'], user_id=ids['owner'], status='active').one()
        db.session.add(BandMembership(band_id=ids['band'], user_id=ids['entrant'], role='member', status='active'))
        if kind == 'media':
            song = Song(band_id=ids['band'], created_by_membership_id=owner_membership.id,
                        title='Vote contention', artist='Fixture', visibility='PUBLIC')
            db.session.add(song); db.session.flush()
            item = Media(band_id=ids['band'], created_by_membership_id=owner_membership.id,
                         song_id=song.id, filename='owned.mp3',
                         file_type='audio', visibility='PUBLIC', upvote_count=0, downvote_count=0, vote_score=0)
        else:
            item = SongSuggestion(band_id=ids['band'], created_by_membership_id=owner_membership.id,
                                  title='Vote contention', artist='Fixture',
                                  link='https://example.invalid/vote', visibility='PUBLIC', thumbs_up=0, thumbs_down=0)
        db.session.add(item); db.session.commit()
        graph = {'band': ids['band'], 'item': item.id, 'owner': ids['owner'], 'entrant': ids['entrant']}
        db.session.remove()

    def principal(*, required=False):
        return principals[request.headers.get('X-Owned-Test-Actor', 'owner')]

    monkeypatch.setattr(writes, 'current_app_user', principal)
    monkeypatch.setattr(votes, 'current_app_user', principal)
    monkeypatch.setattr(storage, 'generate_url', lambda *args, **kwargs: 'https://storage.invalid/owned')
    return app, engine, graph


def _vote(app, graph, kind, actor, value):
    segment = 'media' if kind == 'media' else 'suggestions'
    with app.test_client() as client:
        response = client.put(f"/api/v2/bands/{graph['band']}/{segment}/{graph['item']}/vote",
                              headers={'X-Owned-Test-Actor': actor}, json={'value': value})
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']
        assert set(response.json) == {kind, 'viewer_vote'}
        assert response.json['viewer_vote'] == value
        return response.json[kind]


@pytest.mark.parametrize('kind', ['media', 'suggestion'])
@pytest.mark.parametrize('scenario', ['flip', 'cancel', 'other-account'])
def test_votes_serialize_and_aggregate_the_authoritative_final_rows(invite_pg, monkeypatch, kind, scenario):
    app, engine, graph = _fixture(invite_pg, monkeypatch, kind)
    second_actor = 'entrant' if scenario == 'other-account' else 'owner'
    second_value = 0 if scenario == 'cancel' else -1
    first, second = _ordered_contention(
        app, engine,
        lambda: _vote(app, graph, kind, 'owner', 1),
        lambda: _vote(app, graph, kind, second_actor, second_value),
    )
    assert first['vote_score'] == 1
    assert second['vote_score'] == (-1 if scenario == 'flip' else 0)
    model = Media if kind == 'media' else SongSuggestion
    vote_model = MediaVote if kind == 'media' else SongSuggestionVote
    with app.app_context():
        db.session.remove()
        item = db.session.get(model, graph['item'])
        rows = vote_model.query.filter_by(band_id=graph['band'], is_legacy=False).all()
        assert sorted(row.value for row in rows) == (
            [] if scenario == 'cancel' else [-1] if scenario == 'flip' else [-1, 1])
        assert len({row.created_by_membership_id for row in rows}) == len(rows)
        if kind == 'suggestion':
            assert len({row.voter_user_id for row in rows}) == len(rows)
        upvotes, downvotes = sum(row.value == 1 for row in rows), sum(row.value == -1 for row in rows)
        assert ((item.upvote_count, item.downvote_count, item.vote_score) if kind == 'media'
                else (item.thumbs_up, item.thumbs_down, item.thumbs_up - item.thumbs_down)) == (
                    upvotes, downvotes, upvotes - downvotes)
        event_type = f'content.{"media" if kind == "media" else "suggestion"}_vote.changed'
        audits = AuditEvent.query.filter_by(band_id=graph['band'], event_type=event_type).order_by(AuditEvent.id).all()
        assert [row.actor_user_id for row in audits] == [graph['owner'], graph[second_actor]]
        assert [(row.target_type, row.target_id) for row in audits] == [(model.__name__, str(item.id))] * 2
        assert [json.loads(row.details_json) for row in audits] == [
            {'value': 1, 'changed': True}, {'value': second_value, 'changed': True}]
