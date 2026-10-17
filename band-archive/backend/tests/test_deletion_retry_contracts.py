"""Independent, storage-mocked regression contracts for durable deletion.

Only private in-memory fixtures and synthetic object keys are used here.  The
bounded storage method is blocked by default, even if a test forgets its mock.
"""
from datetime import date, timedelta
import json

from botocore.exceptions import ClientError
import pytest
from sqlalchemy import event, insert, text

from deletion_service import utcnow
from deletion_worker import _claim_one, process_task
from extensions import db
from models import (Announcement, AppUser, AuditEvent, Band, BandMembership, DirectUploadSession,
                    GalleryImage, Media, Member, PersonalLog, R2DeletionTask,
                    Rehearsal, Song, SongSuggestion, Comment,
                    MultipartUploadSession, PersonalLogMultipartUploadSession)
from storage import storage
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_durable_deletion_tasks import deletion_app, signer, OWNER_SUB


PATHS = {'media': 'media', 'personal_log': 'personal-logs', 'gallery': 'gallery'}
MODELS = {'media': Media, 'personal_log': PersonalLog, 'gallery': GalleryImage}
OPERATOR_SUB = '77777777-7777-7777-7777-777777777777'


@pytest.fixture(autouse=True)
def no_unmocked_bounded_delete(monkeypatch):
    def unexpected(_key):
        raise AssertionError('A test must explicitly mock every bounded storage delete')
    monkeypatch.setattr(storage, 'delete_bounded', unexpected)


@pytest.fixture
def authenticated_client(deletion_app, signer):
    _install_jwks(deletion_app, [[_jwk(signer, 'key-1')]])
    deletion_app.config['PG_R2_DELETION_WORKER_ENABLED'] = True
    return deletion_app.test_client()


def headers(signer, subject=OWNER_SUB, email='user@example.com', **claims):
    return {'Authorization': f'Bearer {_token(signer, sub=subject, email=email, **claims)}'}


def seed_file(app, kind, **overrides):
    with app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        actor = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        membership = BandMembership.query.filter_by(band_id=band.id, user_id=actor.id).one()
        values = {'band_id': band.id, 'created_by_membership_id': membership.id,
                  'filename': 'original.mp4', 'object_key': f'bands/{band.id}/media/original.mp4'}
        if kind == 'media':
            values.update(song_id=Song.query.filter_by(band_id=band.id).first().id,
                          file_type='video', transcoding_status='completed',
                          audio_filename='sound.m4a', audio_object_key=f'bands/{band.id}/media/sound.m4a',
                          video_720_filename='720.mp4', video_720_object_key=f'bands/{band.id}/media/720.mp4')
        elif kind == 'personal_log':
            # Member has no authored-membership column; seed the explicitly
            # scoped fixture through Core, as existing v2 member tests do.
            member_id = db.session.execute(insert(Member).values(
                band_id=band.id, name='Profile', instrument='Guitar')).inserted_primary_key[0]
            values.update(member_id=member_id, title='Practice', file_type='video',
                          transcoding_status='completed',
                          object_key=f'bands/{band.id}/personal_logs/original.mp4',
                          audio_filename='sound.m4a', audio_object_key=f'bands/{band.id}/personal_logs/sound.m4a')
        else:
            values.update(filename='image.jpg', object_key=f'bands/{band.id}/gallery/image.jpg')
        values.update(overrides)
        target = MODELS[kind](**values)
        db.session.add(target)
        db.session.commit()
        return band.id, target.id


def enqueue(client, signer, band_id, kind, resource_id):
    response = client.delete(f'/api/v2/bands/{band_id}/{PATHS[kind]}/{resource_id}',
                             headers=headers(signer))
    assert response.status_code == 202, response.get_json()
    return response.get_json()['task_id']


@pytest.mark.parametrize('kind', PATHS)
def test_all_file_kinds_complete_atomically_clear_sql_manifest_and_reuse_completed_task(
        deletion_app, signer, authenticated_client, monkeypatch, kind):
    band_id, resource_id = seed_file(deletion_app, kind)
    task_id = enqueue(authenticated_client, signer, band_id, kind, resource_id)
    calls = []
    monkeypatch.setattr(storage, 'delete_bounded', calls.append)
    with deletion_app.app_context():
        expected_keys = list(db.session.get(R2DeletionTask, task_id).object_keys)
        claimed = _claim_one(120)
        assert claimed[0] == task_id
        assert process_task(*claimed) is True
        task = db.session.get(R2DeletionTask, task_id)
        assert task.status == 'completed'
        assert task.actor_user_id is None
        assert task.object_keys is None
        assert task.lease_token is None and task.lease_expires_at is None
        assert task.completed_at is not None
        assert db.session.get(MODELS[kind], resource_id) is None
        # JSON "null" is not SQL NULL and would violate the production CHECK.
        assert db.session.execute(text('SELECT object_keys IS NULL FROM r2_deletion_task WHERE id = :id'),
                                  {'id': task_id}).scalar() == 1
    assert calls == expected_keys
    duplicate = authenticated_client.delete(
        f'/api/v2/bands/{band_id}/{PATHS[kind]}/{resource_id}', headers=headers(signer))
    assert duplicate.status_code == 200
    assert duplicate.get_json()['task_id'] == task_id
    assert duplicate.get_json()['status'] == 'completed'
    assert calls == expected_keys
    encoded = json.dumps(duplicate.get_json())
    assert all(key not in encoded for key in expected_keys)


@pytest.mark.parametrize('kind', ('media', 'personal_log'))
def test_partial_failure_retains_target_manifest_and_retries_missing_objects(
        deletion_app, signer, authenticated_client, monkeypatch, kind):
    band_id, resource_id = seed_file(deletion_app, kind)
    task_id = enqueue(authenticated_client, signer, band_id, kind, resource_id)
    calls, deleted = [], set()
    fail_once = [True]

    def delete(key):
        calls.append(key)
        if len(calls) == 2 and fail_once[0]:
            fail_once[0] = False
            raise TimeoutError('synthetic storage timeout')
        if key in deleted:
            raise ClientError({'Error': {'Code': 'NoSuchKey'},
                               'ResponseMetadata': {'HTTPStatusCode': 404}}, 'DeleteObject')
        deleted.add(key)

    monkeypatch.setattr(storage, 'delete_bounded', delete)
    with deletion_app.app_context():
        expected = list(db.session.get(R2DeletionTask, task_id).object_keys)
        assert process_task(*_claim_one(120)) is False
        db.session.expire_all()
        task = db.session.get(R2DeletionTask, task_id)
        assert task.status == 'retry_wait' and task.error_code == 'storage_unavailable'
        assert task.object_keys == expected
        assert task.lease_token is None and task.lease_expires_at is None
        assert db.session.get(MODELS[kind], resource_id) is not None
        task.available_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        db.session.remove()  # a new process/session has no in-memory progress
        assert process_task(*_claim_one(120)) is True
        assert db.session.get(MODELS[kind], resource_id) is None
        assert db.session.get(R2DeletionTask, task_id).attempt_count == 2
    assert deleted == set(expected)
    assert calls[:2] == expected[:2]
    assert calls[2:] == expected


def test_expired_lease_is_reclaimed_and_stale_token_never_touches_storage(
        deletion_app, signer, authenticated_client, monkeypatch):
    band_id, resource_id = seed_file(deletion_app, 'media')
    task_id = enqueue(authenticated_client, signer, band_id, 'media', resource_id)
    calls = []
    monkeypatch.setattr(storage, 'delete_bounded', calls.append)
    with deletion_app.app_context():
        old_claim = _claim_one(120)
        task = db.session.get(R2DeletionTask, task_id)
        task.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        assert process_task(*old_claim) is False
        assert calls == []
        new_claim = _claim_one(120)
        assert new_claim[0] == old_claim[0] and new_claim[1] != old_claim[1]
        assert process_task(*old_claim) is False
        assert calls == []
        assert process_task(*new_claim) is True
        assert len(calls) == 3


def test_final_db_commit_failure_preserves_tombstone_and_replays_already_deleted_objects(
        deletion_app, signer, authenticated_client, monkeypatch):
    band_id, resource_id = seed_file(deletion_app, 'media')
    task_id = enqueue(authenticated_client, signer, band_id, 'media', resource_id)
    deleted, calls = set(), []

    def delete(key):
        calls.append(key)
        if key in deleted:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'DeleteObject')
        deleted.add(key)

    monkeypatch.setattr(storage, 'delete_bounded', delete)
    with deletion_app.app_context():
        claimed = _claim_one(120)
        expected = list(db.session.get(R2DeletionTask, task_id).object_keys)
        session = db.session()

        def fail_final_commit(orm_session):
            if any(isinstance(item, R2DeletionTask) and item.status == 'completed'
                   for item in orm_session.dirty):
                raise RuntimeError('synthetic final database commit failure')

        event.listen(session, 'before_commit', fail_final_commit)
        try:
            with pytest.raises(RuntimeError, match='synthetic final database'):
                process_task(*claimed)
        finally:
            event.remove(session, 'before_commit', fail_final_commit)
            db.session.rollback()  # the worker supervisor does this on crash
        db.session.remove()
        task = db.session.get(R2DeletionTask, task_id)
        assert task.status == 'processing' and task.object_keys == expected
        assert db.session.get(Media, resource_id) is not None
        task.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        assert process_task(*_claim_one(120)) is True
        assert db.session.get(Media, resource_id) is None
        assert db.session.get(R2DeletionTask, task_id).object_keys is None
    assert calls == expected + expected


@pytest.mark.parametrize('actor', ('author', 'admin', 'left', 'unverified_operator', 'disabled_operator'))
def test_deletion_and_task_status_do_not_grant_author_admin_or_invalid_account_bypass(
        deletion_app, signer, authenticated_client, actor):
    band_id, resource_id = seed_file(deletion_app, 'gallery')
    task_id = enqueue(authenticated_client, signer, band_id, 'gallery', resource_id)
    with deletion_app.app_context():
        subject = {'author': '33333333-3333-3333-3333-333333333333',
                   'admin': '22222222-2222-2222-2222-222222222222',
                   'left': '33333333-3333-3333-3333-333333333333'}.get(actor, OPERATOR_SUB)
        user = AppUser.query.filter_by(supabase_user_id=subject).one()
        email = user.email
        membership = BandMembership.query.filter_by(band_id=band_id, user_id=user.id).first()
        if actor == 'author':
            db.session.get(GalleryImage, resource_id).created_by_membership_id = membership.id
        if actor == 'left':
            membership.status = 'left'
        if actor.endswith('operator'):
            user.is_platform_admin = True
            user.email_verified = actor != 'unverified_operator'
            if actor == 'disabled_operator':
                user.status = 'disabled'
        db.session.commit()
    auth = headers(signer, subject, email, email_verified=actor != 'unverified_operator')
    denied = authenticated_client.delete(f'/api/v2/bands/{band_id}/gallery/{resource_id}', headers=auth)
    assert denied.status_code in (401, 403)
    status = authenticated_client.get(f'/api/v2/bands/{band_id}/deletion-tasks/{task_id}', headers=auth)
    assert status.status_code in (401, 403, 404)
    if actor.endswith('operator'):
        status = authenticated_client.get(f'/api/v2/platform/deletion-tasks/{task_id}', headers=auth)
        assert status.status_code in (401, 403)
    with deletion_app.app_context():
        assert db.session.get(GalleryImage, resource_id) is not None
        assert R2DeletionTask.query.count() == 1


@pytest.mark.parametrize('bad_key', ('bands/999/media/wrong.mp4', 'bands/1/media/../wrong.mp4',
                                    'https://example.com/video.mp4', 'media/legacy.mp4',
                                    'bands/1/gallery/wrong.mp4'))
def test_invalid_or_untrusted_legacy_keys_fail_before_queue_or_storage(
        deletion_app, signer, authenticated_client, bad_key):
    band_id, resource_id = seed_file(deletion_app, 'media', object_key=bad_key)
    response = authenticated_client.delete(f'/api/v2/bands/{band_id}/media/{resource_id}',
                                           headers=headers(signer))
    assert response.status_code == 409
    with deletion_app.app_context():
        assert R2DeletionTask.query.count() == 0
        assert db.session.get(Media, resource_id) is not None


@pytest.mark.parametrize('phase', ('queued', 'processing'))
@pytest.mark.parametrize('kind', ('media', 'personal_log'))
def test_audio_work_blocks_deletion_without_creating_tombstone(
        deletion_app, signer, authenticated_client, phase, kind):
    band_id, resource_id = seed_file(deletion_app, kind, transcoding_status=phase)
    response = authenticated_client.delete(f'/api/v2/bands/{band_id}/{PATHS[kind]}/{resource_id}',
                                           headers=headers(signer))
    assert response.status_code == 409
    with deletion_app.app_context():
        assert R2DeletionTask.query.count() == 0


def test_live_upload_reference_blocks_deletion_and_cross_band_status_hides_task(
        deletion_app, signer, authenticated_client):
    band_id, resource_id = seed_file(deletion_app, 'media')
    with deletion_app.app_context():
        target = db.session.get(Media, resource_id)
        db.session.add(DirectUploadSession(
            band_id=band_id, created_by_membership_id=target.created_by_membership_id,
            kind='media', object_key=target.audio_object_key, original_filename='sound.m4a',
            content_type='audio/mp4', declared_bytes=16, song_id=target.song_id,
            capability_token_hash='synthetic-test-hash', status='initiated',
            expires_at=utcnow() + timedelta(minutes=5)))
        db.session.commit()
    response = authenticated_client.delete(f'/api/v2/bands/{band_id}/media/{resource_id}',
                                           headers=headers(signer))
    assert response.status_code == 409
    with deletion_app.app_context():
        assert R2DeletionTask.query.count() == 0
        DirectUploadSession.query.delete()
        db.session.commit()
    task_id = enqueue(authenticated_client, signer, band_id, 'media', resource_id)
    with deletion_app.app_context():
        other_band_id = Band.query.filter_by(slug='band-b').one().id
    assert authenticated_client.get(
        f'/api/v2/bands/{other_band_id}/deletion-tasks/{task_id}', headers=headers(signer)).status_code == 404


@pytest.mark.parametrize('kind', PATHS)
def test_real_completed_direct_result_foreign_key_is_detached_in_final_transaction(
        deletion_app, signer, authenticated_client, monkeypatch, kind):
    band_id, resource_id = seed_file(deletion_app, kind)
    with deletion_app.app_context():
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys = ON')
            assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
        target = db.session.get(MODELS[kind], resource_id)
        result_column = {'media': 'media_id', 'personal_log': 'personal_log_id',
                         'gallery': 'result_gallery_image_id'}[kind]
        values = {result_column: resource_id}
        if kind == 'media':
            values['song_id'] = target.song_id
        elif kind == 'personal_log':
            values['member_id'] = target.member_id
        upload = DirectUploadSession(
            band_id=band_id, created_by_membership_id=target.created_by_membership_id,
            kind=kind, object_key=target.object_key, original_filename=target.filename,
            content_type='application/octet-stream', declared_bytes=32,
            capability_token_hash='synthetic-completed-capability', status='completed', **values)
        db.session.add(upload)
        db.session.commit()
        upload_id = upload.id
    task_id = enqueue(authenticated_client, signer, band_id, kind, resource_id)
    monkeypatch.setattr(storage, 'delete_bounded', lambda _key: None)
    with deletion_app.app_context():
        assert process_task(*_claim_one(120)) is True
        assert db.session.get(MODELS[kind], resource_id) is None
        upload = db.session.get(DirectUploadSession, upload_id)
        assert upload is None or getattr(upload, result_column) is None
        assert db.session.get(R2DeletionTask, task_id).status == 'completed'
        assert db.session.execute(text('PRAGMA foreign_key_check')).all() == []


@pytest.mark.parametrize('kind', PATHS)
def test_nonmember_superadmin_uses_only_targeted_operator_queue_and_safe_status(
        deletion_app, signer, authenticated_client, kind):
    band_id, resource_id = seed_file(deletion_app, kind, visibility='MEMBERS')
    with deletion_app.app_context():
        operator = AppUser.query.filter_by(supabase_user_id=OPERATOR_SUB).one()
        operator.is_platform_admin = True
        db.session.commit()
    auth = headers(signer, OPERATOR_SUB, 'operator@example.com')
    # Check the private reader before creating a tombstone, otherwise an
    # empty response could simply be deletion filtering rather than RBAC.
    if kind in {'media', 'personal_log'}:
        private_path = 'media' if kind == 'media' else 'personal-logs'
        private_read = authenticated_client.get(
            f'/api/v2/bands/{band_id}/{private_path}/{resource_id}/processing', headers=auth)
        assert private_read.status_code in (403, 404)
    else:
        private_read = authenticated_client.get(f'/api/v2/bands/{band_id}/gallery', headers=auth)
        assert private_read.status_code in (200, 403, 404)
        if private_read.status_code == 200:
            assert private_read.get_json()['gallery'] == []
    ordinary = authenticated_client.delete(
        f'/api/v2/bands/{band_id}/{PATHS[kind]}/{resource_id}', headers=auth)
    assert ordinary.status_code == 403
    response = authenticated_client.post(f'/api/v2/platform/bands/{band_id}/deletions',
        headers=auth, json={'resource_type': kind, 'resource_id': resource_id,
                           'reason': 'Confirmed synthetic moderation request', 'confirm': True})
    assert response.status_code == 202
    task_id = response.get_json()['task_id']
    response = authenticated_client.get(f'/api/v2/platform/deletion-tasks/{task_id}', headers=auth)
    assert response.status_code == 200
    payload = response.get_json()
    assert not ({'object_keys', 'url', 'audio_url', 'actor_user_id', 'lease_token'} & payload.keys())
    assert authenticated_client.get(
        f'/api/v2/bands/{band_id}/deletion-tasks/{task_id}', headers=auth).status_code == 404
    # Having an operator deletion capability must not unlock private readers.
    reader = authenticated_client.get(f'/api/v2/bands/{band_id}/gallery', headers=auth)
    assert reader.status_code in (200, 403, 404)
    if reader.status_code == 200:
        assert reader.get_json()['gallery'] == []
    with deletion_app.app_context():
        event_row = AuditEvent.query.filter_by(event_type='platform.content.deletion.queued').one()
        details = json.loads(event_row.details_json)
        assert details['reason'] == 'Confirmed synthetic moderation request'
        assert not ({'object_keys', 'url', 'audio_url'} & details.keys())


@pytest.mark.parametrize('payload_change', ({'confirm': False}, {'confirm': 'true'}, {'resource_id': True},
                                           {'reason': ''}, {'reason': ' '}, {'reason': 'x' * 501},
                                           {'resource_type': 'app_user'}, {'object_key': 'media/forged.mp4'}))
def test_operator_rejects_ambiguous_confirmation_or_client_supplied_keys_without_task(
        deletion_app, signer, authenticated_client, payload_change):
    band_id, resource_id = seed_file(deletion_app, 'gallery')
    with deletion_app.app_context():
        AppUser.query.filter_by(supabase_user_id=OPERATOR_SUB).one().is_platform_admin = True
        db.session.commit()
    body = {'resource_type': 'gallery', 'resource_id': resource_id, 'reason': 'Synthetic cleanup', 'confirm': True}
    body.update(payload_change)
    response = authenticated_client.post(f'/api/v2/platform/bands/{band_id}/deletions',
        headers=headers(signer, OPERATOR_SUB, 'operator@example.com'), json=body)
    assert response.status_code == 400
    with deletion_app.app_context():
        assert R2DeletionTask.query.count() == 0
        assert db.session.get(GalleryImage, resource_id) is not None
        assert AuditEvent.query.filter_by(event_type='platform.content.deletion.queued').count() == 0


def test_operator_announcement_clear_is_synchronous_worker_independent_and_keeps_singleton(
        deletion_app, signer, authenticated_client):
    with deletion_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        operator = AppUser.query.filter_by(supabase_user_id=OPERATOR_SUB).one()
        operator.is_platform_admin = True
        owner = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        membership = BandMembership.query.filter_by(band_id=band.id, user_id=owner.id).one()
        target = Announcement(band_id=band.id, created_by_membership_id=membership.id,
                              content='Synthetic private announcement', visibility='MEMBERS')
        db.session.add(target)
        db.session.commit()
        band_id, target_id = band.id, target.id
    deletion_app.config['PG_R2_DELETION_WORKER_ENABLED'] = False
    response = authenticated_client.post(f'/api/v2/platform/bands/{band_id}/deletions',
        headers=headers(signer, OPERATOR_SUB, 'operator@example.com'),
        json={'resource_type': 'announcement', 'resource_id': target_id,
              'reason': 'Synthetic clear request', 'confirm': True})
    assert response.status_code == 200
    assert response.get_json() == {'status': 'completed', 'resource_type': 'announcement', 'resource_id': target_id}
    with deletion_app.app_context():
        target = db.session.get(Announcement, target_id)
        assert target is not None and target.content == '' and target.visibility == 'MEMBERS'
        assert R2DeletionTask.query.count() == 0
        audit_row = AuditEvent.query.filter_by(event_type='platform.content.deletion.completed').one()
        assert json.loads(audit_row.details_json)['reason'] == 'Synthetic clear request'


def test_worker_revalidates_manifest_against_late_shared_reference_before_storage(
        deletion_app, signer, authenticated_client, monkeypatch):
    band_id, resource_id = seed_file(deletion_app, 'media')
    task_id = enqueue(authenticated_client, signer, band_id, 'media', resource_id)
    with deletion_app.app_context():
        target = db.session.get(Media, resource_id)
        owner_membership = target.created_by_membership_id
        db.session.add(GalleryImage(band_id=band_id, created_by_membership_id=owner_membership,
                                   filename='shared.jpg', object_key=target.audio_object_key))
        db.session.commit()
        calls = []
        monkeypatch.setattr(storage, 'delete_bounded', calls.append)
        assert process_task(*_claim_one(120)) is False
        assert calls == []
        task = db.session.get(R2DeletionTask, task_id)
        assert task.status == 'blocked' and task.error_code == 'key_conflict'
        assert task.object_keys is not None
        assert db.session.get(Media, resource_id) is not None


@pytest.mark.parametrize('kind', ('media', 'personal_log'))
def test_pending_deletion_blocks_audio_retry_and_atomic_queue_claim(
        deletion_app, signer, authenticated_client, kind):
    from media_processing import (_claim_record, retry_audio_processing_record,
                                  MEDIA_SPEC, PERSONAL_LOG_SPEC)
    band_id, resource_id = seed_file(deletion_app, kind)
    enqueue(authenticated_client, signer, band_id, kind, resource_id)
    spec = MEDIA_SPEC if kind == 'media' else PERSONAL_LOG_SPEC
    with deletion_app.app_context():
        target = db.session.get(MODELS[kind], resource_id)
        with pytest.raises(RuntimeError, match='Deletion is pending'):
            retry_audio_processing_record(deletion_app, target, spec)
        target.transcoding_status = 'queued'  # simulate a stale pre-deletion retry
        db.session.commit()
        assert _claim_record(spec, resource_id) is None
        assert db.session.get(MODELS[kind], resource_id).transcoding_status == 'queued'


@pytest.mark.parametrize('kind', ('media', 'personal_log'))
def test_completed_multipart_result_foreign_key_is_detached_before_final_delete(
        deletion_app, signer, authenticated_client, monkeypatch, kind):
    band_id, resource_id = seed_file(deletion_app, kind)
    model = MultipartUploadSession if kind == 'media' else PersonalLogMultipartUploadSession
    with deletion_app.app_context():
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys = ON')
        target = db.session.get(MODELS[kind], resource_id)
        values = {'song_id': target.song_id, 'media_id': target.id} if kind == 'media' else {
            'member_id': target.member_id, 'personal_log_id': target.id, 'title': target.title}
        upload = model(band_id=band_id, created_by_membership_id=target.created_by_membership_id,
                       r2_upload_id='synthetic-upload-id', object_key=target.object_key,
                       original_filename=target.filename, content_type='video/mp4', declared_bytes=32,
                       status='completed', **values)
        db.session.add(upload)
        db.session.commit()
        upload_id = upload.id
    enqueue(authenticated_client, signer, band_id, kind, resource_id)
    monkeypatch.setattr(storage, 'delete_bounded', lambda _key: None)
    with deletion_app.app_context():
        assert process_task(*_claim_one(120)) is True
        assert db.session.get(MODELS[kind], resource_id) is None
        upload = db.session.get(model, upload_id)
        assert upload is None or getattr(upload, 'media_id' if kind == 'media' else 'personal_log_id') is None
        assert db.session.execute(text('PRAGMA foreign_key_check')).all() == []


@pytest.mark.parametrize('parent_kind', ('song', 'rehearsal', 'member'))
@pytest.mark.parametrize('upload_status', ('initiated', 'completing', 'expiring', 'completed', 'failed'))
def test_parent_delete_blocks_live_capabilities_but_cleans_safe_terminal_history(
        deletion_app, signer, authenticated_client, parent_kind, upload_status):
    with deletion_app.app_context():
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys = ON')
        band = Band.query.filter_by(slug='band-a').one()
        owner = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        membership = BandMembership.query.filter_by(band_id=band.id, user_id=owner.id).one()
        if parent_kind == 'member':
            target_id = db.session.execute(insert(Member).values(
                band_id=band.id, name='Synthetic target', instrument='Guitar')).inserted_primary_key[0]
            target = db.session.get(Member, target_id)
            values = {'kind': 'personal_log', 'member_id': target_id}
            path = 'members'
        elif parent_kind == 'rehearsal':
            target = Rehearsal(band_id=band.id, created_by_membership_id=membership.id,
                               title='Synthetic rehearsal', date=date(2026, 10, 17))
            db.session.add(target)
            db.session.flush()
            song = Song.query.filter_by(band_id=band.id).first()
            values = {'kind': 'media', 'song_id': song.id, 'rehearsal_id': target.id}
            path = 'rehearsals'
        else:
            target = Song(band_id=band.id, created_by_membership_id=membership.id,
                          title='Synthetic empty song', artist='Test')
            db.session.add(target)
            db.session.flush()
            values = {'kind': 'media', 'song_id': target.id}
            path = 'songs'
        upload = DirectUploadSession(
            band_id=band.id, created_by_membership_id=membership.id,
            object_key=f'bands/{band.id}/media/synthetic-parent.mp4',
            original_filename='synthetic-parent.mp4', content_type='video/mp4', declared_bytes=32,
            capability_token_hash='synthetic-parent-capability', status=upload_status,
            expires_at=utcnow() - timedelta(days=1), **values)
        db.session.add(upload)
        db.session.commit()
        band_id, target_id, upload_id, target_model = band.id, target.id, upload.id, type(target)
    response = authenticated_client.delete(f'/api/v2/bands/{band_id}/{path}/{target_id}',
                                           headers=headers(signer))
    live = upload_status in {'initiated', 'completing', 'expiring'}
    assert response.status_code == (409 if live else 200), response.get_json()
    with deletion_app.app_context():
        assert (db.session.get(target_model, target_id) is not None) is live
        assert (db.session.get(DirectUploadSession, upload_id) is not None) is live
        assert R2DeletionTask.query.count() == 0
        assert db.session.execute(text('PRAGMA foreign_key_check')).all() == []


@pytest.mark.parametrize('kind,model', (('song', Song), ('rehearsal', Rehearsal), ('member', Member),
                                      ('comment', Comment), ('suggestion', SongSuggestion)))
def test_operator_all_text_types_are_synchronous_without_worker_and_audited(
        deletion_app, signer, authenticated_client, kind, model):
    with deletion_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        AppUser.query.filter_by(supabase_user_id=OPERATOR_SUB).one().is_platform_admin = True
        actor = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        membership = BandMembership.query.filter_by(band_id=band.id, user_id=actor.id).one()
        values = {'band_id': band.id, 'created_by_membership_id': membership.id}
        if kind == 'song':
            values.update(title='Synthetic text song', artist='Test')
        elif kind == 'rehearsal':
            values.update(title='Synthetic text rehearsal', date=date(2026, 10, 17))
        elif kind == 'member':
            values.pop('created_by_membership_id')
            values.update(name='Synthetic profile', instrument='Guitar')
        elif kind == 'comment':
            # A real parent media preserves the independent comment contract.
            song = Song.query.filter_by(band_id=band.id).first()
            media = Media(band_id=band.id, created_by_membership_id=membership.id,
                          song_id=song.id, filename='parent.mp3', file_type='audio',
                          object_key=f'bands/{band.id}/media/parent.mp3')
            db.session.add(media)
            db.session.flush()
            values.update(media_id=media.id, author='Synthetic author', content='Synthetic comment')
        else:
            values.update(title='Synthetic suggestion', artist='Test', link='https://example.test/track')
        target_id = db.session.execute(insert(model).values(**values)).inserted_primary_key[0]
        db.session.commit()
        band_id = band.id
    deletion_app.config['PG_R2_DELETION_WORKER_ENABLED'] = False
    response = authenticated_client.post(f'/api/v2/platform/bands/{band_id}/deletions',
        headers=headers(signer, OPERATOR_SUB, 'operator@example.com'),
        json={'resource_type': kind, 'resource_id': target_id, 'reason': 'Synthetic moderation', 'confirm': True})
    assert response.status_code == 200, response.get_json()
    assert response.get_json() == {'status': 'completed', 'resource_type': kind, 'resource_id': target_id}
    with deletion_app.app_context():
        assert db.session.get(model, target_id) is None
        assert R2DeletionTask.query.count() == 0
        audit_row = AuditEvent.query.filter_by(event_type='platform.content.deletion.completed').one()
        assert json.loads(audit_row.details_json)['reason'] == 'Synthetic moderation'


@pytest.mark.parametrize('kind', PATHS)
def test_only_validated_import_band_resolves_legacy_bare_filename_manifest(
        deletion_app, signer, authenticated_client, monkeypatch, kind):
    band_id, resource_id = seed_file(deletion_app, kind)
    prefix = {'media': 'media', 'personal_log': 'personal_logs', 'gallery': 'gallery'}[kind]
    with deletion_app.app_context():
        Band.query.filter_by(id=band_id).one().is_legacy_import = True
        target = db.session.get(MODELS[kind], resource_id)
        target.object_key = None
        if kind != 'gallery':
            target.audio_object_key = None
        if kind == 'media':
            target.video_720_object_key = None
        expected = [f'{prefix}/{target.filename}']
        if kind != 'gallery':
            expected.append(f'{prefix}/{target.audio_filename}')
        if kind == 'media':
            expected.append(f'{prefix}/{target.video_720_filename}')
        db.session.commit()
    task_id = enqueue(authenticated_client, signer, band_id, kind, resource_id)
    calls = []
    monkeypatch.setattr(storage, 'delete_bounded', calls.append)
    with deletion_app.app_context():
        assert db.session.get(R2DeletionTask, task_id).object_keys == expected
        assert process_task(*_claim_one(120)) is True
    assert calls == expected


def test_crashed_final_attempt_is_blocked_on_reclaim_without_unbounded_storage_replay(
        deletion_app, signer, authenticated_client, monkeypatch):
    band_id, resource_id = seed_file(deletion_app, 'media')
    task_id = enqueue(authenticated_client, signer, band_id, 'media', resource_id)
    deletion_app.config['R2_DELETION_MAX_ATTEMPTS'] = 2
    calls = []
    monkeypatch.setattr(storage, 'delete_bounded', calls.append)
    with deletion_app.app_context():
        task = db.session.get(R2DeletionTask, task_id)
        task.status = 'processing'
        task.attempt_count = 2
        task.lease_token = 'expired-final-attempt'
        task.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        assert _claim_one(120) is None
        task = db.session.get(R2DeletionTask, task_id)
        assert task.status == 'blocked' and task.attempt_count == 2
        assert task.lease_token is None and task.lease_expires_at is None
        assert task.object_keys is not None
        assert db.session.get(Media, resource_id) is not None
        assert calls == []


@pytest.mark.parametrize('kind', PATHS)
def test_corrupt_cross_band_completed_result_pointer_is_rejected_before_any_storage_delete(
        deletion_app, signer, authenticated_client, monkeypatch, kind):
    band_id, resource_id = seed_file(deletion_app, kind)
    with deletion_app.app_context():
        target = db.session.get(MODELS[kind], resource_id)
        other_band = Band.query.filter_by(slug='band-b').one()
        other_member = BandMembership.query.filter_by(band_id=other_band.id).one()
        values = {'media_id': resource_id, 'song_id': Song.query.filter_by(band_id=other_band.id).first().id}
        if kind == 'personal_log':
            member_id = db.session.execute(insert(Member).values(
                band_id=other_band.id, name='Other profile', instrument='Bass')).inserted_primary_key[0]
            values = {'personal_log_id': resource_id, 'member_id': member_id}
        elif kind == 'gallery':
            values = {'result_gallery_image_id': resource_id}
        db.session.add(DirectUploadSession(
            band_id=other_band.id, created_by_membership_id=other_member.id,
            kind=kind, object_key=f'bands/{other_band.id}/media/unrelated.mp4',
            original_filename='unrelated.mp4', content_type='video/mp4', declared_bytes=32,
            capability_token_hash='synthetic-corrupt-result', status='completed', **values))
        db.session.commit()
    calls = []
    monkeypatch.setattr(storage, 'delete_bounded', calls.append)
    response = authenticated_client.delete(f'/api/v2/bands/{band_id}/{PATHS[kind]}/{resource_id}',
                                           headers=headers(signer))
    assert response.status_code in (202, 409)
    with deletion_app.app_context():
        if response.status_code == 202:
            task_id = response.get_json()['task_id']
            assert process_task(*_claim_one(120)) is False
            assert db.session.get(R2DeletionTask, task_id).status == 'blocked'
        assert db.session.get(MODELS[kind], resource_id) is not None
        assert calls == []
