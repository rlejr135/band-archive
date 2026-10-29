"""Retryable multipart initiation when expired direct cleanup cannot finish.

These HTTP contracts use real SQLite persistence and authenticated requests.
Only storage calls are doubled; PostgreSQL contention is covered separately.
"""

from datetime import datetime, timedelta, timezone

import pytest

from direct_upload_service import recover_expiring_session
from extensions import db
from models import (Band, BandMembership, DirectUploadSession, Media, Member,
                    MultipartUploadSession, PersonalLogMultipartUploadSession, Song)
from storage import storage
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_content_writes import principals, write_client  # noqa: F401


RETRY_ERROR = {'error': 'An expired upload is still being cleaned up. Please retry shortly.'}


def _prepare(app, *, quota=25, state='initiated'):
    with app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        band.storage_quota_bytes = quota
        membership = BandMembership.query.filter_by(
            band_id=band.id, role='member', status='active').one()
        song = Song.query.filter_by(band_id=band.id, title='A members').one()
        member = Member(band_id=band.id, name='Cleanup fixture', instrument='Bass')
        db.session.add(member)
        db.session.add(Media(band_id=band.id, created_by_membership_id=membership.id,
            song_id=song.id, filename='used.mp3', original_filename='used.mp3',
            file_type='audio', file_size=5, object_key=f'bands/{band.id}/media/used.mp3'))
        reservation = DirectUploadSession(
            band_id=band.id, created_by_membership_id=membership.id,
            kind='media', song_id=song.id, original_filename='expired.mp4',
            object_key=f'bands/{band.id}/media/expired.mp4', content_type='video/mp4',
            declared_bytes=10, capability_token_hash='synthetic-only', status=state,
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
        db.session.add(reservation)
        db.session.commit()
        return band.id, song.id, member.id, reservation.id


def _post(client, headers, ids, target):
    band_id, song_id, member_id, _ = ids
    fields = ({'song_id': song_id} if target == 'media' else
              {'member_id': member_id, 'title': 'Practice fixture'})
    return client.post(f'/api/v2/bands/{band_id}/uploads/multipart/initiate',
        headers=headers, json={'filename': 'new.mp4', 'content_type': 'video/mp4',
                               'declared_bytes': 10, **fields})


def _multipart_rows(band_id):
    return (MultipartUploadSession.query.filter_by(band_id=band_id).all(),
            PersonalLogMultipartUploadSession.query.filter_by(band_id=band_id).all())


@pytest.mark.parametrize('target', ['media', 'personal_log'])
def test_multipart_cleanup_failure_is_retryable_and_durably_recovers(
        access_app, write_client, principals, monkeypatch, target):
    ids = _prepare(access_app)
    band_id, _, _, expired_id = ids
    created, deleted = [], []
    monkeypatch.setattr(storage, 'create_multipart_upload',
                        lambda key, content_type: created.append(key) or 'synthetic-upload')

    def fail_delete(key):
        deleted.append(key)
        raise RuntimeError('synthetic storage unavailable')

    monkeypatch.setattr(storage, 'delete', fail_delete)
    response = _post(write_client, principals['member'], ids, target)
    assert response.status_code == 503
    assert response.get_json() == RETRY_ERROR
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.vary
    assert created == [] and len(deleted) == 1
    with access_app.app_context():
        db.session.remove()
        reservation = db.session.get(DirectUploadSession, expired_id)
        assert reservation.status == 'expiring' and reservation.declared_bytes == 10
        assert reservation.completion_started_at is not None
        assert _multipart_rows(band_id) == ([], [])
        assert Media.query.filter_by(band_id=band_id).count() == 1

    monkeypatch.setattr(storage, 'delete', deleted.append)
    with access_app.app_context():
        assert recover_expiring_session(db.session.get(DirectUploadSession, expired_id)) is True
    with access_app.app_context():
        db.session.remove()
        assert db.session.get(DirectUploadSession, expired_id).status == 'expired'
    repeated = _post(write_client, principals['member'], ids, target)
    assert repeated.status_code == 201
    assert len(created) == 1 and len(deleted) == 2
    with access_app.app_context():
        db.session.remove()
        media, personal = _multipart_rows(band_id)
        expected, other = (media, personal) if target == 'media' else (personal, media)
        assert len(expected) == 1 and other == []
        assert expected[0].declared_bytes == 10


@pytest.mark.parametrize('target', ['media', 'personal_log'])
@pytest.mark.parametrize('quota,status', [(25, 201), (19, 409)])
def test_expiring_cleanup_marker_remains_reserved_during_multipart_retry(
        access_app, write_client, principals, monkeypatch, target, quota, status):
    ids = _prepare(access_app, quota=quota, state='expiring')
    band_id, _, _, expired_id = ids
    created = []
    monkeypatch.setattr(storage, 'create_multipart_upload',
                        lambda key, content_type: created.append(key) or 'synthetic-upload')
    monkeypatch.setattr(storage, 'delete', lambda key: pytest.fail('initiate must retain expiring markers'))
    response = _post(write_client, principals['member'], ids, target)
    assert response.status_code == status
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.vary
    if status == 409:
        assert response.get_json() == {'error': 'Band storage quota exceeded.'}
    assert len(created) == (1 if status == 201 else 0)
    with access_app.app_context():
        db.session.remove()
        reservation = db.session.get(DirectUploadSession, expired_id)
        assert reservation.status == 'expiring' and reservation.declared_bytes == 10
        media, personal = _multipart_rows(band_id)
        expected, other = (media, personal) if target == 'media' else (personal, media)
        assert len(expected) == (1 if status == 201 else 0) and other == []
        reserved = reservation.declared_bytes + sum(row.declared_bytes for row in expected)
        assert reserved == (20 if status == 201 else 10)
        assert 5 + reserved <= quota
