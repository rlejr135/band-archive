"""Owned-local PostgreSQL upload reservation contention contracts.

Four success-path cases use two real HTTP initiates. The two cleanup-failure
cases use one HTTP initiate and an independent transaction observing the same
band lock and retained reservation. R2 operations and actor resolution are
doubles; quota queries, row locks, reservations and commits remain real.
"""

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text

from errors import register_error_handlers
from extensions import db
from models import (AppUser, Band, BandMembership, DirectUploadSession, Media,
                    MultipartUploadSession, PersonalLogMultipartUploadSession, Song)
import routes.uploads as uploads
import upload_initiation_service as initiation
from storage import storage
import upload_authorization_service as authorization
from tests.test_invite_link_concurrency import invite_pg
from tests.test_data_api_security_migration import private_pg17  # noqa: F401


pytestmark = [pytest.mark.postgres_integration, pytest.mark.skipif(
    os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly provisioned owned localhost PG17 only')]


def _fixture(invite_pg, monkeypatch, *, expired=False, quota=15):
    app, engine, ids = invite_pg
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True,
                      UPLOAD_POLICY_REQUIRED=False)
    app.register_blueprint(uploads.uploads_bp)
    register_error_handlers(app)
    calls = {'delete': [], 'multipart': [], 'presign': []}

    def actor(required=True):
        return db.session.get(AppUser, ids['owner'])

    monkeypatch.setattr(uploads, 'current_app_user', actor)
    monkeypatch.setattr(initiation, 'current_app_user', actor)
    monkeypatch.setattr(authorization, 'current_app_user', actor)

    def presign(key, **kwargs):
        calls['presign'].append(key)
        return 'https://storage.invalid/upload'

    def multipart(key, content_type):
        calls['multipart'].append(key)
        return 'synthetic-upload-' + str(len(calls['multipart']))

    monkeypatch.setattr(storage, 'generate_upload_url', presign)
    monkeypatch.setattr(storage, 'create_multipart_upload', multipart)
    monkeypatch.setattr(storage, 'delete', lambda key: calls['delete'].append(key))
    with app.app_context():
        band = db.session.get(Band, ids['band'])
        band.storage_quota_bytes = quota
        membership = BandMembership.query.filter_by(
            band_id=band.id, user_id=ids['owner']).one()
        song = Song(band_id=band.id, created_by_membership_id=membership.id,
                    title='Quota fixture', artist='Fixture')
        db.session.add(song)
        db.session.flush()
        db.session.add(Media(band_id=band.id, created_by_membership_id=membership.id,
                             song_id=song.id, filename='used.mp3', file_type='audio',
                             original_filename='used.mp3', file_size=5,
                             object_key=f'bands/{band.id}/media/used.mp3'))
        expired_id = None
        if expired:
            reservation = DirectUploadSession(
                band_id=band.id, created_by_membership_id=membership.id,
                kind='media', song_id=song.id, original_filename='expired.mp4',
                object_key=f'bands/{band.id}/media/expired.mp4',
                content_type='video/mp4', declared_bytes=10,
                capability_token_hash='synthetic-only',
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
            db.session.add(reservation)
            db.session.flush()
            expired_id = reservation.id
        db.session.commit()
        song_id = song.id
        db.session.remove()
    return app, engine, ids['band'], song_id, expired_id, calls


def _post(app, band_id, song_id, kind):
    with app.test_client() as client:
        response = client.post(f'/api/v2/bands/{band_id}/uploads/{kind}/initiate', json={
            'filename': 'new.mp4', 'content_type': 'video/mp4',
            'declared_bytes': 10, 'song_id': song_id,
        })
        return response.status_code, response.get_json()


def _contend(app, engine, first_operation, second_operation, ready, release):
    """Observe PostgreSQL's blocking graph before releasing the first writer."""
    local = threading.local()
    second_query = threading.Event()
    pids = {}

    def before(connection, cursor, statement, parameters, context, executemany):
        label = getattr(local, 'label', None)
        if (label is None or getattr(local, 'seen', False)
                or 'FOR UPDATE' not in statement.upper()
                or not re.search(r'\bFROM\s+band\b', statement, re.IGNORECASE)):
            return
        local.seen = True
        pids[label] = connection.connection.driver_connection.info.backend_pid
        if label == 'second':
            second_query.set()

    def worker(label, operation):
        local.label, local.seen = label, False
        with app.app_context():
            try:
                db.session.execute(text("SET LOCAL statement_timeout = '30s'"))
                db.session.execute(text("SET LOCAL lock_timeout = '25s'"))
                return operation()
            finally:
                db.session.remove()

    event.listen(engine, 'before_cursor_execute', before)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='quota-owned-pg') as pool:
            first = pool.submit(worker, 'first', first_operation)
            try:
                assert ready.wait(15), 'first initiate did not reach the quota boundary'
                second = pool.submit(worker, 'second', second_operation)
                assert second_query.wait(10), 'second transaction did not attempt the band lock'
                assert pids['first'] != pids['second']
                deadline = time.monotonic() + 10
                with engine.connect() as observer:
                    while True:
                        blockers = observer.execute(text('SELECT pg_blocking_pids(:pid)'),
                                                    {'pid': pids['second']}).scalar_one()
                        if pids['first'] in blockers:
                            break
                        assert time.monotonic() < deadline, 'no PostgreSQL blocking relationship observed'
                        release.wait(0.01)
                release.set()
                return first.result(timeout=30), second.result(timeout=30)
            finally:
                release.set()
    finally:
        event.remove(engine, 'before_cursor_execute', before)


def _reservations(band_id):
    direct = DirectUploadSession.query.filter(
        DirectUploadSession.band_id == band_id,
        DirectUploadSession.status.in_(('initiated', 'completing', 'expiring'))).all()
    media = MultipartUploadSession.query.filter(
        MultipartUploadSession.band_id == band_id,
        MultipartUploadSession.status.in_(('initiated', 'completing', 'aborting'))).all()
    personal = PersonalLogMultipartUploadSession.query.filter(
        PersonalLogMultipartUploadSession.band_id == band_id,
        PersonalLogMultipartUploadSession.status.in_(('initiated', 'completing', 'aborting'))).all()
    return direct + media + personal


@pytest.mark.parametrize('first_kind,second_kind,expired', [
    ('direct', 'direct', False), ('direct', 'multipart', False),
    ('multipart', 'direct', False), ('direct', 'direct', True),
], ids=['direct-direct', 'direct-multipart', 'multipart-direct', 'expired-cleanup'])
def test_quota_reservations_serialize_after_accounting(
        invite_pg, monkeypatch, first_kind, second_kind, expired):
    app, engine, band_id, song_id, expired_id, calls = _fixture(
        invite_pg, monkeypatch, expired=expired)
    ready, release = threading.Event(), threading.Event()
    factory_name = 'create_direct_session' if first_kind == 'direct' else 'create_session'
    original_factory = getattr(initiation, factory_name)
    gate_lock = threading.Lock()
    gated = False

    def create(*args, **kwargs):
        nonlocal gated
        with gate_lock:
            first_call = not gated
            gated = True
        if first_call:
            # All quota reads and expiry cleanup have happened. A premature
            # cleanup commit would now make the blocking proof fail.
            ready.set()
            assert release.wait(30), 'coordinator did not release reservation creation'
        return original_factory(*args, **kwargs)

    monkeypatch.setattr(initiation, factory_name, create)
    first, second = _contend(
        app, engine,
        lambda: _post(app, band_id, song_id, first_kind),
        lambda: _post(app, band_id, song_id, second_kind), ready, release)
    assert first[0] == 201
    assert second == (409, {'error': 'Band storage quota exceeded.'})
    with app.app_context():
        db.session.remove()
        reservations = _reservations(band_id)
        assert len(reservations) == 1
        assert sum(row.declared_bytes for row in reservations) == 10
        assert sum(row.file_size for row in Media.query.filter_by(band_id=band_id)) == 5
        assert 5 + sum(row.declared_bytes for row in reservations) <= 15
        if expired:
            assert db.session.get(DirectUploadSession, expired_id).status == 'expired'
    assert len(calls['multipart']) == (1 if first_kind == 'multipart' else 0)
    assert len(calls['presign']) == (1 if first_kind == 'direct' else 0)
    assert len(calls['delete']) == (1 if expired else 0)


@pytest.mark.parametrize('kind', ['direct', 'multipart'])
def test_cleanup_failure_retains_quota_and_retryable_initiation_error_contract(
        invite_pg, monkeypatch, kind):
    app, engine, band_id, song_id, expired_id, calls = _fixture(
        invite_pg, monkeypatch, expired=True, quota=25)
    ready, release = threading.Event(), threading.Event()

    def delete(key):
        calls['delete'].append(key)
        ready.set()
        assert release.wait(30), 'coordinator did not release failed cleanup'
        raise RuntimeError('synthetic storage unavailable')

    monkeypatch.setattr(storage, 'delete', delete)

    def initiate():
        return _post(app, band_id, song_id, kind)

    def observe_reservation():
        db.session.execute(text('SELECT id FROM band WHERE id=:id FOR UPDATE'), {'id': band_id})
        rows = _reservations(band_id)
        observed = [(row.id, row.status, row.declared_bytes) for row in rows]
        db.session.commit()
        return observed

    # The second participant observes quota in an independent transaction;
    # it is deliberately not claimed to be a second failing HTTP initiate.
    first, observed = _contend(app, engine, initiate, observe_reservation, ready, release)
    assert first == (503, {'error': 'An expired upload is still being cleaned up. Please retry shortly.'})
    assert len(observed) == 1
    assert observed[0][0] == expired_id and observed[0][2] == 10
    assert observed[0][1] in ('initiated', 'expiring')
    with app.app_context():
        db.session.remove()
        reservation = db.session.get(DirectUploadSession, expired_id)
        assert reservation.status == 'expiring'
        rows = _reservations(band_id)
        assert len(rows) == 1 and rows[0].id == expired_id
        assert sum(row.declared_bytes for row in rows) == 10
        assert 5 + sum(row.declared_bytes for row in rows) <= 25
        assert MultipartUploadSession.query.filter_by(band_id=band_id).count() == 0
        assert PersonalLogMultipartUploadSession.query.filter_by(band_id=band_id).count() == 0
    assert len(calls['delete']) == 1
    assert calls['multipart'] == [] and calls['presign'] == []
