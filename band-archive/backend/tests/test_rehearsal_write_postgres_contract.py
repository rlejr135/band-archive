"""Owned-local PostgreSQL Rehearsal/Song races; only JWT resolution is synthetic.

The coordinator proves pg_blocking_pids between separate sessions before
releasing the first Band lock. HTTP, authorization, relations, audits and commit
remain real. Companion HTTP/JWT tests cover actual token resolution.
"""

from dataclasses import dataclass
from datetime import date
import json
import os

import pytest

from errors import register_error_handlers
from extensions import db
from models import AppUser, AuditEvent, BandMembership, Rehearsal, R2DeletionTask, Song
import routes.content_writes as writes
import rehearsal_service as rehearsals
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
        songs = [Song(band_id=ids['band'], created_by_membership_id=membership.id, visibility='PUBLIC',
                      title=f'Owned rehearsal song {index}', artist='Fixture artist') for index in range(2)]
        rehearsal = Rehearsal(band_id=ids['band'], created_by_membership_id=membership.id,
                              visibility='PUBLIC', title='Owned rehearsal race', date=date(2026, 11, 3),
                              memo='Original memo', songs=[songs[0]])
        db.session.add_all([*songs, rehearsal])
        db.session.commit()
        graph = {'band': ids['band'], 'owner': ids['owner'], 'author': membership.id,
                 'rehearsal': rehearsal.id, 'songs': [song.id for song in songs]}
        db.session.remove()
    monkeypatch.setattr(writes, 'current_app_user', lambda required=True: principal)
    monkeypatch.setattr(rehearsals, 'current_app_user', lambda required=True: principal)
    return app, engine, graph


def _request(app, graph, *, data=None, delete_song=False):
    with app.test_client() as client:
        response = (client.delete(f"/api/v2/bands/{graph['band']}/songs/{graph['songs'][1]}")
                    if delete_song else client.patch(
                        f"/api/v2/bands/{graph['band']}/rehearsals/{graph['rehearsal']}", json=data))
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']
        return response.status_code, response.get_json()


def _assert_response(result, graph, song_ids, *, memo='Original memo', location=None):
    status, payload = result
    assert status == 200 and set(payload) == {'rehearsal'}
    row = payload['rehearsal']
    assert row['id'] == graph['rehearsal'] and row['title'] == 'Owned rehearsal race'
    assert row['date'] == '2026-11-03' and row['rehearsal_type'] == 'UNCLASSIFIED'
    assert row['visibility'] == row['effective_visibility'] == 'PUBLIC'
    assert row['can_edit'] is row['can_delete'] is row['can_manage'] is True
    assert row['memo'] == memo and row['location'] == location
    assert [song['id'] for song in row['songs']] == song_ids


def _audits():
    return AuditEvent.query.filter(AuditEvent.event_type.in_(
        ('content.rehearsal.updated', 'content.song.deleted'))).order_by(AuditEvent.id).all()


def test_two_rehearsal_updates_serialize_associations_and_preserve_other_fields(invite_pg, monkeypatch):
    app, engine, graph = _fixture(invite_pg, monkeypatch)
    first, second = _ordered_contention(
        app, engine,
        lambda: _request(app, graph, data={'song_ids': [graph['songs'][1]], 'memo': 'First memo'}),
        lambda: _request(app, graph, data={'song_ids': [graph['songs'][0]], 'location': 'Second room'}),
    )
    _assert_response(first, graph, [graph['songs'][1]], memo='First memo')
    _assert_response(second, graph, [graph['songs'][0]], memo='First memo', location='Second room')
    with app.app_context():
        db.session.remove()
        rehearsal = db.session.get(Rehearsal, graph['rehearsal'])
        assert [song.id for song in rehearsal.songs] == [graph['songs'][0]]
        assert rehearsal.created_by_membership_id == graph['author']
        assert rehearsal.memo == 'First memo' and rehearsal.location == 'Second room'
        audits = _audits()
        assert [row.event_type for row in audits] == ['content.rehearsal.updated'] * 2
        assert [row.actor_user_id for row in audits] == [graph['owner']] * 2
        assert [json.loads(row.details_json) for row in audits] == [
            {'fields': ['memo', 'song_ids']}, {'fields': ['location', 'song_ids']}]
        assert R2DeletionTask.query.count() == 0


@pytest.mark.parametrize('delete_first', [False, True], ids=['link-first', 'delete-first'])
def test_song_deletion_and_rehearsal_linking_share_one_serialized_order(invite_pg, monkeypatch, delete_first):
    app, engine, graph = _fixture(invite_pg, monkeypatch)
    link = lambda: _request(app, graph, data={'song_ids': [graph['songs'][1]]})
    delete = lambda: _request(app, graph, delete_song=True)
    first, second = _ordered_contention(
        app, engine, delete if delete_first else link, link if delete_first else delete)
    deleted, linked = (first, second) if delete_first else (second, first)
    assert deleted == (200, {'deleted': True, 'id': graph['songs'][1]})
    if delete_first:
        assert linked == (404, {'error': 'One or more songs are unavailable for this rehearsal.'})
    else:
        _assert_response(linked, graph, [graph['songs'][1]])
    with app.app_context():
        db.session.remove()
        assert db.session.get(Song, graph['songs'][1]) is None
        rehearsal = db.session.get(Rehearsal, graph['rehearsal'])
        # The regular band Song DELETE permits same-band secondary links and
        # removes them atomically; the platform deletion policy is different.
        assert [song.id for song in rehearsal.songs] == ([graph['songs'][0]] if delete_first else [])
        audits = _audits()
        assert [row.event_type for row in audits] == (
            ['content.song.deleted'] if delete_first else ['content.rehearsal.updated', 'content.song.deleted'])
        assert [row.actor_user_id for row in audits] == [graph['owner']] * len(audits)
        assert audits[-1].target_type == 'Song' and audits[-1].target_id == str(graph['songs'][1])
        assert R2DeletionTask.query.count() == 0
