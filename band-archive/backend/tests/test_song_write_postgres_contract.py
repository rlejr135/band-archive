"""Real owned-local PostgreSQL Song write races; JWT resolution alone is synthetic.

The coordinator proves pg_blocking_pids between independent sessions before
releasing the first Band lock. HTTP dispatch, scope, row locks, audit and commit
remain real; actual JWT resolution is covered by the companion HTTP tests.
"""

from dataclasses import dataclass
import json
import os

import pytest

from errors import register_error_handlers
from extensions import db
from models import AppUser, AuditEvent, BandMembership, R2DeletionTask, Song
import routes.content_writes as writes
import song_service as songs
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


def _fixture(invite_pg, monkeypatch):
    app, engine, ids = invite_pg
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True)
    app.register_blueprint(writes.content_writes_bp)
    register_error_handlers(app)
    with app.app_context():
        owner = db.session.get(AppUser, ids['owner'])
        principal = Principal(**{name: getattr(owner, name) for name in Principal.__dataclass_fields__})
        membership = BandMembership.query.filter_by(
            band_id=ids['band'], user_id=ids['owner'], status='active').one()
        song = Song(band_id=ids['band'], created_by_membership_id=membership.id,
                    visibility='PUBLIC', title='Owned song race', artist='Fixture artist',
                    memo='Original memo', genre='Original genre')
        db.session.add(song)
        db.session.commit()
        graph = {'band': ids['band'], 'song': song.id, 'owner': ids['owner'], 'author': membership.id}
        db.session.remove()
    monkeypatch.setattr(writes, 'current_app_user', lambda required=True: principal)
    monkeypatch.setattr(songs, 'current_app_user', lambda required=True: principal)
    return app, engine, graph


def _request(app, graph, *, data=None, delete=False):
    with app.test_client() as client:
        path = f"/api/v2/bands/{graph['band']}/songs/{graph['song']}"
        response = client.delete(path) if delete else client.patch(path, json=data)
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']
        return response.status_code, response.get_json()


def _assert_song_response(result, graph, *, memo='Original memo', genre='Original genre'):
    status, payload = result
    assert status == 200 and set(payload) == {'song'}
    song = payload['song']
    assert set(song) == {'id', 'title', 'artist', 'status', 'chords', 'link', 'memo', 'genre',
                         'difficulty', 'upvote_count', 'downvote_count', 'vote_score', 'created_at',
                         'updated_at', 'visibility', 'effective_visibility', 'can_edit',
                         'can_delete', 'can_manage', 'rehearsals'}
    assert song['id'] == graph['song'] and song['title'] == 'Owned song race'
    assert song['artist'] == 'Fixture artist' and song['memo'] == memo and song['genre'] == genre
    assert song['visibility'] == song['effective_visibility'] == 'PUBLIC'
    assert song['can_edit'] is song['can_delete'] is song['can_manage'] is True
    assert song['rehearsals'] == []


def _audits():
    return AuditEvent.query.filter(AuditEvent.event_type.in_(
        ('content.song.updated', 'content.song.deleted'))).order_by(AuditEvent.id).all()


def test_two_song_updates_serialize_and_preserve_nonoverlapping_fields(invite_pg, monkeypatch):
    app, engine, graph = _fixture(invite_pg, monkeypatch)
    first, second = _ordered_contention(
        app, engine,
        lambda: _request(app, graph, data={'memo': 'First memo'}),
        lambda: _request(app, graph, data={'genre': 'Second genre'}),
    )
    _assert_song_response(first, graph, memo='First memo')
    _assert_song_response(second, graph, memo='First memo', genre='Second genre')
    with app.app_context():
        db.session.remove()
        song = db.session.get(Song, graph['song'])
        assert (song.memo, song.genre, song.created_by_membership_id) == (
            'First memo', 'Second genre', graph['author'])
        audits = _audits()
        assert [row.event_type for row in audits] == ['content.song.updated'] * 2
        assert [row.actor_user_id for row in audits] == [graph['owner']] * 2
        assert [json.loads(row.details_json) for row in audits] == [
            {'fields': ['memo']}, {'fields': ['genre']}]
        assert R2DeletionTask.query.count() == 0


@pytest.mark.parametrize('delete_first', [False, True], ids=['update-first', 'delete-first'])
def test_song_update_and_delete_serialize_without_resurrecting_song(invite_pg, monkeypatch, delete_first):
    app, engine, graph = _fixture(invite_pg, monkeypatch)
    update = lambda: _request(app, graph, data={'memo': 'Updated before deletion'})
    delete = lambda: _request(app, graph, delete=True)
    first, second = _ordered_contention(
        app, engine, delete if delete_first else update, update if delete_first else delete)
    deleted, updated = (first, second) if delete_first else (second, first)
    assert deleted == (200, {'deleted': True, 'id': graph['song']})
    if delete_first:
        assert updated == (404, {'error': 'Song not found'})
    else:
        _assert_song_response(updated, graph, memo='Updated before deletion')
    with app.app_context():
        db.session.remove()
        assert db.session.get(Song, graph['song']) is None
        audits = _audits()
        expected = ['content.song.deleted'] if delete_first else ['content.song.updated', 'content.song.deleted']
        assert [row.event_type for row in audits] == expected
        assert [row.actor_user_id for row in audits] == [graph['owner']] * len(expected)
        assert [(row.target_type, row.target_id) for row in audits] == [('Song', str(graph['song']))] * len(expected)
        if not delete_first:
            assert json.loads(audits[0].details_json) == {'fields': ['memo']}
        assert audits[-1].details_json is None
        assert R2DeletionTask.query.count() == 0
