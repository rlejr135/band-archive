"""Real SQLite persistence across the existing multi-commit upload workflow.

R2 and worker wakeup are doubles. PostgreSQL locking and authorization remain
the responsibility of the existing upload/leave/restriction integration tests.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import event

import direct_upload_service
from extensions import db
from errors import ValidationError
import media_processing
from models import (Band, BandMembership, DirectUploadSession, Media,
                    MultipartUploadPart, MultipartUploadSession, Song)
import multipart_upload_service
import upload_materialization_service as materialization
from tests.test_band_access_phase6 import access_app


def _session(mode):
    band = Band.query.filter_by(slug='band-a').one()
    membership = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
    song = Song.query.filter_by(band_id=band.id, title='A public').one()
    common = dict(band_id=band.id, created_by_membership_id=membership.id,
                  object_key=f'bands/{band.id}/media/recovery.mp4',
                  original_filename='recovery.mp4', content_type='video/mp4',
                  declared_bytes=10, song_id=song.id, capability_token_hash='test-only')
    if mode == 'direct':
        session = DirectUploadSession(kind='media', **common)
    else:
        session = MultipartUploadSession(r2_upload_id='test-r2-id', is_legacy=False, **common)
    db.session.add(session)
    db.session.flush()
    if mode == 'multipart':
        db.session.add(MultipartUploadPart(session_id=session.id, part_number=1,
            etag='test-etag', uploaded_bytes=10, acknowledged_at=datetime.now(timezone.utc)))
    db.session.commit()
    return session


@pytest.mark.parametrize('mode', ['direct', 'multipart'])
@pytest.mark.parametrize('failure', ['record_commit', 'worker_wakeup', 'completion_commit'])
def test_materialization_failure_preserves_current_recovery_without_duplicate_records(
        access_app, monkeypatch, mode, failure):
    with access_app.app_context():
        session = _session(mode)
        session_id, model = session.id, type(session)
        events = []
        monkeypatch.setattr(multipart_upload_service.storage, 'complete_multipart_upload',
                            lambda *_args: events.append('r2_complete'))

        def size(_key):
            events.append('head')
            return 10

        def wake(_app, _identity):
            events.append('wake')
            if failure == 'worker_wakeup':
                raise RuntimeError('injected worker_wakeup')

        monkeypatch.setattr(media_processing, 'start_audio_processing', wake)
        commit_count = 0

        def before_commit(_session):
            nonlocal commit_count
            commit_count += 1
            events.append(f'commit_{commit_count}')
            fail_at = {'record_commit': 2, 'completion_commit': 3}.get(failure)
            if commit_count == fail_at:
                raise RuntimeError(f'injected {failure}')

        orm_session = db.session()
        event.listen(orm_session, 'before_commit', before_commit)
        complete = (direct_upload_service.complete_direct_session if mode == 'direct'
                    else multipart_upload_service.complete_session)
        create = (materialization.materialize_direct_upload if mode == 'direct'
                  else materialization.materialize_multipart_upload)
        try:
            with pytest.raises(RuntimeError, match=f'injected {failure}'):
                complete(session, size, create)
        finally:
            event.remove(orm_session, 'before_commit', before_commit)

        # Remove the identity map before verifying durable state in a new session.
        db.session.remove()
        session = db.session.get(model, session_id)
        object_key = session.object_key
        assert session.status == 'initiated'
        records = Media.query.filter_by(object_key=session.object_key).all()
        assert len(records) == (0 if failure == 'record_commit' else 1)
        if failure == 'record_commit':
            assert 'wake' not in events
        else:
            assert events.index('commit_2') < events.index('wake')
            before_retry = list(events)
            item, status = complete(session, size, create)
            assert status == 200 and item.id == records[0].id
            assert events == before_retry, 'recovered records must not repeat R2 work or worker wakeup'
            db.session.remove()
            assert db.session.get(model, session_id).status == 'completed'
            assert Media.query.filter_by(object_key=object_key).count() == 1


@pytest.mark.parametrize('mode,expected_status', [('direct', 'initiated'), ('multipart', 'failed')])
def test_materialization_validation_error_keeps_existing_target_state_difference(
        access_app, monkeypatch, mode, expected_status):
    with access_app.app_context():
        session = _session(mode)
        session_id, model, object_key = session.id, type(session), session.object_key
        monkeypatch.setattr(multipart_upload_service.storage, 'complete_multipart_upload', lambda *_args: None)

        def reject_visibility(*_args):
            raise ValidationError('injected invalid upload evidence')

        monkeypatch.setattr(materialization, 'upload_session_visibility', reject_visibility)
        complete = (direct_upload_service.complete_direct_session if mode == 'direct'
                    else multipart_upload_service.complete_session)
        create = (materialization.materialize_direct_upload if mode == 'direct'
                  else materialization.materialize_multipart_upload)
        with pytest.raises(ValidationError, match='invalid upload evidence'):
            complete(session, lambda _key: 10, create)
        db.session.remove()
        assert db.session.get(model, session_id).status == expected_status
        assert Media.query.filter_by(object_key=object_key).count() == 0
