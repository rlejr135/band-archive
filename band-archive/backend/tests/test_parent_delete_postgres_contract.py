"""Owned-local PG parent deletion versus upload reservation contracts.

Only JWT resolution and R2 calls are doubled. HTTP dispatch, current database
authority, quota queries, row locks, reservations, audit and commits remain
real. The shared coordinator observes pg_blocking_pids between independent
sessions before releasing the first Band lock; thread timing is not evidence.
No upload completion, storage deletion or background worker is executed.
"""

from dataclasses import dataclass
from datetime import date, datetime
import os

import pytest

from errors import register_error_handlers
from extensions import db
from models import (AppUser, AuditEvent, Band, BandMembership, DirectUploadSession,
                    Media, MultipartUploadSession, PersonalLogMultipartUploadSession,
                    Rehearsal, R2DeletionTask, Song)
import routes.content_writes as writes
import routes.uploads as uploads
from storage import storage
import upload_authorization_service as authorization
import upload_initiation_service as initiation
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


def _fixture(invite_pg, monkeypatch, parent):
    app, engine, ids = invite_pg
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True, UPLOAD_POLICY_REQUIRED=False)
    app.register_blueprint(writes.content_writes_bp)
    app.register_blueprint(uploads.uploads_bp)
    register_error_handlers(app)
    with app.app_context():
        owner = db.session.get(AppUser, ids['owner'])
        principal = Principal(**{name: getattr(owner, name) for name in Principal.__dataclass_fields__})
        membership = BandMembership.query.filter_by(
            band_id=ids['band'], user_id=ids['owner'], status='active').one()
        # A new ten-byte reservation exactly fills this band's quota. There
        # is no existing media, upload, expired cleanup or external R2 work.
        db.session.get(Band, ids['band']).storage_quota_bytes = 10
        song = Song(band_id=ids['band'], created_by_membership_id=membership.id,
                    title='Owned parent upload race', artist='Fixture', visibility='MEMBERS')
        db.session.add(song)
        rehearsal = None
        if parent == 'rehearsal':
            rehearsal = Rehearsal(
                band_id=ids['band'], created_by_membership_id=membership.id,
                title='Owned upload event', date=date(2026, 11, 4), visibility='MEMBERS')
            db.session.add(rehearsal)
        db.session.commit()
        graph = {'band': ids['band'], 'owner': ids['owner'], 'membership': membership.id,
                 'song': song.id, 'rehearsal': rehearsal.id if rehearsal else None}
        db.session.remove()

    # Actual JWT behavior is covered separately by the HTTP/JWT contracts.
    for module in (writes, uploads, authorization, initiation):
        monkeypatch.setattr(module, 'current_app_user', lambda required=True: principal)
    calls = {'presign': [], 'multipart': []}

    def presign(key, **kwargs):
        calls['presign'].append(key)
        return 'https://storage.invalid/owned-parent-upload'

    def multipart(key, content_type):
        calls['multipart'].append((key, content_type))
        return 'owned-parent-upload-id'

    def forbidden(*args, **kwargs):
        pytest.fail('parent reservation contracts must not mutate or inspect actual R2 objects')

    monkeypatch.setattr(storage, 'generate_upload_url', presign)
    monkeypatch.setattr(storage, 'create_multipart_upload', multipart)
    for name in ('delete', 'head', 'abort_multipart_upload', 'complete_multipart_upload'):
        monkeypatch.setattr(storage, name, forbidden)
    assert app.extensions.get('r2_deletion_worker') is None
    return app, engine, graph, calls


def _private(response):
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']
    return response.status_code, response.get_json()


def _delete(app, graph, parent):
    with app.test_client() as client:
        return _private(client.delete(
            f"/api/v2/bands/{graph['band']}/{parent}s/{graph[parent]}"))


def _initiate(app, graph, kind):
    payload = {'filename': 'owned.mp4', 'content_type': 'video/mp4',
               'declared_bytes': 10, 'song_id': graph['song']}
    if graph['rehearsal'] is not None:
        payload['rehearsal_id'] = graph['rehearsal']
    with app.test_client() as client:
        return _private(client.post(
            f"/api/v2/bands/{graph['band']}/uploads/{kind}/initiate", json=payload))


@pytest.mark.parametrize('parent', ['song', 'rehearsal'])
@pytest.mark.parametrize('kind', ['direct', 'multipart'])
@pytest.mark.parametrize('delete_first', [False, True], ids=['initiate-first', 'delete-first'])
def test_parent_deletion_and_upload_reservation_have_one_serialized_order(
        invite_pg, monkeypatch, parent, kind, delete_first):
    app, engine, graph, calls = _fixture(invite_pg, monkeypatch, parent)
    delete = lambda: _delete(app, graph, parent)
    initiate = lambda: _initiate(app, graph, kind)
    first, second = _ordered_contention(
        app, engine, delete if delete_first else initiate, initiate if delete_first else delete)
    deleted, initiated = (first, second) if delete_first else (second, first)
    model = Song if parent == 'song' else Rehearsal
    if delete_first:
        assert deleted == (200, {'deleted': True, 'id': graph[parent]})
        assert initiated == (404, {'error': f'{model.__name__} not found'})
        assert calls == {'presign': [], 'multipart': []}
    else:
        assert deleted == (409, {'error':
            f'Wait for attached uploads to finish cleanup before deleting this {parent}.'})
        assert initiated[0] == 201
        payload = initiated[1]
        assert set(payload) == ({'session_id', 'filename', 'expires_at', 'upload_capability_token',
                                 'capability_header', 'upload_url'} if kind == 'direct' else
                                {'session_id', 'filename', 'expires_at', 'upload_capability_token',
                                 'capability_header', 'part_size', 'max_parts'})
        assert payload['capability_header'] == 'X-Upload-Capability'
        assert isinstance(payload['upload_capability_token'], str) and payload['upload_capability_token']
        datetime.fromisoformat(payload['expires_at'])
        if kind == 'direct':
            assert payload['upload_url'] == 'https://storage.invalid/owned-parent-upload'
        else:
            assert payload['part_size'] > 0 and payload['max_parts'] > 0
        assert len(calls['presign']) == (1 if kind == 'direct' else 0)
        assert len(calls['multipart']) == (1 if kind == 'multipart' else 0)

    with app.app_context():
        db.session.remove()
        assert (db.session.get(model, graph[parent]) is None) is delete_first
        # Deleting a Rehearsal leaves the separately required Song intact.
        if parent == 'rehearsal':
            assert db.session.get(Song, graph['song']) is not None
        direct = DirectUploadSession.query.filter_by(band_id=graph['band']).all()
        multipart = MultipartUploadSession.query.filter_by(band_id=graph['band']).all()
        assert len(direct) == (int(not delete_first) if kind == 'direct' else 0)
        assert len(multipart) == (int(not delete_first) if kind == 'multipart' else 0)
        assert PersonalLogMultipartUploadSession.query.filter_by(band_id=graph['band']).count() == 0
        if not delete_first:
            reservation = (direct or multipart)[0]
            assert reservation.id == initiated[1]['session_id']
            assert (reservation.band_id, reservation.created_by_membership_id,
                    reservation.song_id, reservation.rehearsal_id) == (
                        graph['band'], graph['membership'], graph['song'], graph['rehearsal'])
            assert reservation.status == 'initiated' and reservation.declared_bytes == 10
            assert reservation.object_key.startswith(f"bands/{graph['band']}/media/")
            assert reservation.object_key.endswith('/' + initiated[1]['filename'])
            if kind == 'direct':
                assert reservation.kind == 'media'
            else:
                assert reservation.r2_upload_id == 'owned-parent-upload-id' and reservation.is_legacy is False
        audits = AuditEvent.query.filter_by(
            band_id=graph['band'], event_type=f'content.{parent}.deleted').all()
        assert len(audits) == int(delete_first)
        if delete_first:
            assert (audits[0].actor_user_id, audits[0].target_type, audits[0].target_id) == (
                graph['owner'], model.__name__, str(graph[parent]))
        assert Media.query.filter_by(band_id=graph['band']).count() == 0
        assert R2DeletionTask.query.count() == 0
