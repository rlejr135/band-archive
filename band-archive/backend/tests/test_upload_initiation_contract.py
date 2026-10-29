"""HTTP initiation contracts across the existing reservation transaction.

SQLite rows, session factories, audit flushes and commits are real. Actor
resolution, the prepared policy assertion and R2 calls are controlled doubles;
these tests complement, rather than replace, JWT and PostgreSQL race coverage.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from errors import ValidationError
from extensions import db
from member_profile_service import ensure_member_profile
from models import (AppUser, AuditEvent, Band, BandMembership, DirectUploadSession,
                    MultipartUploadSession, PersonalLogMultipartUploadSession,
                    Song)
import routes.uploads as uploads
from storage import storage
import upload_authorization_service as authorization
from tests.test_band_access_phase6 import access_app  # noqa: F401


MODES = ['direct_media', 'direct_personal', 'gallery', 'multipart_media', 'multipart_personal']
ASSERTION = {'version': 'initiation-contract',
             'batch_id': '00000000-0000-0000-0000-000000000001',
             'visibility': 'MEMBERS', 'rights_confirmed': True}


@pytest.fixture
def initiation(access_app, monkeypatch):
    app = access_app
    with app.app_context():
        band = Band.query.filter_by(slug='band-a').with_for_update().one()
        band.storage_quota_bytes = 100
        membership = BandMembership.query.filter_by(
            band_id=band.id, role='member', status='active').with_for_update().one()
        actor = db.session.get(AppUser, membership.user_id)
        actor.email_verified = True
        db.session.flush()
        member = ensure_member_profile(membership)
        db.session.commit()
        ids = (band.id, membership.id, actor.id, member.id,
               Song.query.filter_by(band_id=band.id, title='A members').one().id)
    events, fault = [], {'stage': None}

    def actor(*, required):
        assert required is True
        events.append('actor')
        return db.session.get(AppUser, ids[2])

    def policy(data, user):
        events.append('policy')
        assert user.id == ids[2]
        if fault['stage'] == 'policy':
            raise ValidationError('Policy rejected.', 403, code='fixture_policy_rejected')
        return dict(ASSERTION)

    cleanup = uploads.expire_direct_sessions_for_band

    def expire(band_id, *, commit=True):
        events.append('cleanup')
        assert band_id == ids[0] and commit is False
        return cleanup(band_id, commit=commit)

    def factory(original):
        def create(*args, **kwargs):
            events.append('create')
            assert kwargs['commit'] is False
            value = original(*args, **kwargs)
            if fault['stage'] == 'create':
                raise RuntimeError('Injected session creation failure.')
            return value
        return create

    record = uploads.record_upload_assertion

    def audit(session, membership, assertion):
        events.append('audit')
        return record(session, membership, assertion)

    def target(original, label):
        def validate(*args, **kwargs):
            events.append(label)
            return original(*args, **kwargs)
        return validate

    def before_flush(session, *_args):
        if any(isinstance(row, AuditEvent) for row in session.new):
            events.append('audit_flush')
            if fault['stage'] == 'audit_flush':
                raise RuntimeError('Injected audit flush failure.')

    def before_commit(_session):
        events.append('commit')
        if fault['stage'] == 'commit':
            raise RuntimeError('Injected commit failure.')

    def rolled_back(_session):
        events.append('rollback')

    def presign(key, **kwargs):
        events.append('presign')
        if fault['stage'] == 'presign':
            raise RuntimeError('Injected presign failure.')
        return 'https://storage.invalid/upload'

    def multipart(key, content_type):
        events.append('r2_create')
        return 'synthetic-multipart'

    def delete(key):
        events.append('r2_delete')
        if fault['stage'] == 'cleanup':
            raise RuntimeError('Injected cleanup failure.')

    monkeypatch.setattr(uploads, 'current_app_user', actor)
    monkeypatch.setattr(authorization, 'current_app_user', actor)
    monkeypatch.setattr(uploads, 'generate_secure_filename', lambda name: 'safe-' + name)
    monkeypatch.setattr(uploads, 'validate_upload_assertion', policy)
    monkeypatch.setattr(uploads, 'expire_direct_sessions_for_band', expire)
    monkeypatch.setattr(uploads, 'create_direct_session', factory(uploads.create_direct_session))
    monkeypatch.setattr(uploads, 'create_session', factory(uploads.create_session))
    monkeypatch.setattr(uploads, 'record_upload_assertion', audit)
    monkeypatch.setattr(uploads, 'validate_media_upload_target',
                        target(uploads.validate_media_upload_target, 'media_target'))
    monkeypatch.setattr(uploads, 'validate_personal_log_upload_target',
                        target(uploads.validate_personal_log_upload_target, 'personal_target'))
    monkeypatch.setattr(storage, 'generate_upload_url', presign)
    monkeypatch.setattr(storage, 'create_multipart_upload', multipart)
    monkeypatch.setattr(storage, 'delete', delete)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    event.listen(Session, 'after_rollback', rolled_back)
    try:
        yield SimpleNamespace(app=app, ids=ids, events=events, fault=fault)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
        event.remove(Session, 'after_rollback', rolled_back)


def _post(harness, mode, **changes):
    filename = 'picture.jpg' if mode == 'gallery' else 'recording.mp4'
    payload = {'filename': filename, 'declared_bytes': 10,
               'content_type': 'image/jpeg' if mode == 'gallery' else 'video/mp4'}
    if mode != 'gallery':
        payload.update({'member_id': harness.ids[3], 'title': 'Practice'}
                       if mode.endswith('personal') else {'song_id': harness.ids[4]})
    payload.update(changes)
    path = ('gallery/direct' if mode == 'gallery' else
            'uploads/multipart' if mode.startswith('multipart') else 'uploads/direct')
    with harness.app.test_client() as client:
        return client.post(f'/api/v2/bands/{harness.ids[0]}/{path}/initiate', json=payload)


def _stored(harness):
    with harness.app.app_context():
        db.session.remove()
        rows = []
        for model, kind in ((DirectUploadSession, None), (MultipartUploadSession, 'media'),
                            (PersonalLogMultipartUploadSession, 'personal_log')):
            for row in model.query.filter_by(band_id=harness.ids[0]).all():
                rows.append({'id': row.id, 'kind': kind or row.kind, 'key': row.object_key,
                             'bytes': row.declared_bytes, 'status': row.status,
                             'creator': row.created_by_membership_id})
        audits = [{'target': row.target_id, 'actor': row.actor_user_id,
                   'details': json.loads(row.details_json)}
                  for row in AuditEvent.query.filter_by(
                      band_id=harness.ids[0], event_type='upload_policy_confirmed').all()]
        return rows, audits


@pytest.mark.parametrize('mode', MODES)
def test_initiation_preserves_actor_policy_cleanup_audit_commit_and_response_order(initiation, mode):
    response = _post(initiation, mode)
    assert response.status_code == 201
    payload = response.get_json()
    is_multipart = mode.startswith('multipart')
    assert set(payload) == ({'session_id', 'filename', 'expires_at', 'upload_capability_token',
                             'capability_header', 'part_size', 'max_parts'} if is_multipart else
                            {'session_id', 'filename', 'expires_at', 'upload_capability_token',
                             'capability_header', 'upload_url'})
    assert payload['capability_header'] == 'X-Upload-Capability'
    assert isinstance(payload['upload_capability_token'], str) and payload['upload_capability_token']
    assert payload['expires_at']
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.vary
    target = ([] if mode == 'gallery' else
              ['personal_target' if mode.endswith('personal') else 'media_target'])
    assert initiation.events == ['actor', 'actor', 'actor', 'policy', 'cleanup'] + target + [
        'create'] + (['r2_create'] if is_multipart else []) + ['audit', 'audit_flush', 'commit'] + (
        [] if is_multipart else ['presign'])
    rows, audits = _stored(initiation)
    kind = 'gallery' if mode == 'gallery' else 'personal_log' if mode.endswith('personal') else 'media'
    prefix = 'personal_logs' if kind == 'personal_log' else kind
    filename = 'safe-picture.jpg' if mode == 'gallery' else 'safe-recording.mp4'
    assert payload['filename'] == filename
    assert rows == [{'id': payload['session_id'], 'kind': kind,
                     'key': f'bands/{initiation.ids[0]}/{prefix}/{filename}', 'bytes': 10,
                     'status': 'initiated', 'creator': initiation.ids[1]}]
    assert audits == [{'target': payload['session_id'], 'actor': initiation.ids[2], 'details': ASSERTION}]


@pytest.mark.parametrize('mode', ['direct_media', 'gallery', 'multipart_personal'])
def test_policy_rejection_precedes_cleanup_storage_and_reservation(initiation, mode):
    initiation.fault['stage'] = 'policy'
    response = _post(initiation, mode)
    assert response.status_code == 403
    assert response.get_json() == {'error': 'Policy rejected.', 'code': 'fixture_policy_rejected'}
    assert initiation.events == ['actor', 'actor', 'actor', 'policy', 'rollback']
    assert _stored(initiation) == ([], [])


@pytest.mark.parametrize('mode,changes', [
    ('direct_media', {'song_id': 999999}),
    ('direct_personal', {'filename': 'document.pdf'}),
    ('multipart_personal', {'title': 'x' * 201}),
])
def test_quota_rejection_precedes_late_target_type_and_title_validation(initiation, mode, changes):
    with initiation.app.app_context():
        db.session.get(Band, initiation.ids[0]).storage_quota_bytes = 5
        db.session.commit()
    initiation.events.clear()
    response = _post(initiation, mode, **changes)
    assert response.status_code == 409
    assert response.get_json() == {'error': 'Band storage quota exceeded.'}
    assert initiation.events == ['actor', 'actor', 'actor', 'policy', 'cleanup', 'rollback']
    assert _stored(initiation) == ([], [])


@pytest.mark.parametrize('mode', ['direct_media', 'gallery', 'multipart_personal'])
@pytest.mark.parametrize('stage', ['create', 'audit_flush', 'commit'])
def test_initiation_failures_roll_back_flushed_reservation_and_audit(initiation, mode, stage):
    initiation.fault['stage'] = stage
    with pytest.raises(RuntimeError, match='Injected'):
        _post(initiation, mode)
    assert 'rollback' in initiation.events
    assert _stored(initiation) == ([], [])
    assert initiation.events.count('r2_create') == int(mode.startswith('multipart'))
    # Multipart creation precedes DB persistence. Rollback is not an R2 abort.
    assert 'presign' not in initiation.events
    if stage == 'create':
        assert 'audit' not in initiation.events
    else:
        assert initiation.events.index('create') < initiation.events.index('audit')
        assert 'audit_flush' in initiation.events


@pytest.mark.parametrize('mode', ['direct_media', 'gallery'])
def test_direct_presign_failure_occurs_after_durable_reservation_commit(initiation, mode):
    initiation.fault['stage'] = 'presign'
    with pytest.raises(RuntimeError, match='Injected presign failure'):
        _post(initiation, mode)
    assert initiation.events.index('commit') < initiation.events.index('presign')
    rows, audits = _stored(initiation)
    assert len(rows) == len(audits) == 1
    assert rows[0]['bytes'] == 10 and rows[0]['status'] == 'initiated'
    assert audits[0]['target'] == rows[0]['id']


@pytest.mark.parametrize('mode', ['direct_media', 'multipart_media'])
def test_cleanup_marker_is_committed_only_after_reservation_transaction_rollback(initiation, mode):
    with initiation.app.app_context():
        expired = DirectUploadSession(
            band_id=initiation.ids[0], created_by_membership_id=initiation.ids[1],
            kind='media', song_id=initiation.ids[4], original_filename='expired.mp4',
            object_key=f'bands/{initiation.ids[0]}/media/expired.mp4',
            content_type='video/mp4', declared_bytes=10, capability_token_hash='synthetic-only',
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
        db.session.add(expired)
        db.session.commit()
        identity = expired.id
    initiation.events.clear()
    initiation.fault['stage'] = 'cleanup'
    response = _post(initiation, mode)
    assert response.status_code == 503
    assert response.get_json() == {
        'error': 'An expired upload is still being cleaned up. Please retry shortly.'}
    assert initiation.events == ['actor', 'actor', 'actor', 'policy', 'cleanup',
                                 'r2_delete', 'rollback', 'commit']
    rows, audits = _stored(initiation)
    assert audits == []
    assert len(rows) == 1 and rows[0]['id'] == identity
    assert rows[0]['status'] == 'expiring' and rows[0]['bytes'] == 10
