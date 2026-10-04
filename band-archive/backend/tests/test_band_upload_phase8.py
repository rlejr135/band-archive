"""Authenticated band multipart-upload contracts for Phase 8."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Lock
import threading

from botocore.exceptions import ClientError
import pytest

from app import create_app
from config import TestingConfig
from direct_upload_service import (complete_direct_session, create_direct_session,
                                   expire_direct_session_if_needed)
from extensions import db
from multipart_upload_service import recover_sessions
import routes.uploads as upload_routes
from routes.uploads import _v2_direct_item, recover_multipart_upload_sessions
from errors import ValidationError
from models import (AppUser, Band, BandMembership, DirectUploadSession, GalleryImage, Media, Member,
                    MultipartUploadSession, PersonalLog, Song, TenantBackfillState)
from storage import storage
from tests.test_band_access_phase6 import access_app, signer
from tests.test_v2_content_writes import _band_id, principals, write_client


def _song(access_app, band_id):
    with access_app.app_context():
        membership = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        song = Song(band_id=band_id, created_by_membership_id=membership.id,
                    visibility='MEMBERS', title='upload target', artist='band')
        db.session.add(song); db.session.commit()
        return song.id, membership.id


def _initiate(client, band_id, headers, song_id, bytes=10):
    return client.post(f'/api/v2/bands/{band_id}/uploads/multipart/initiate', headers=headers, json={
        'filename': 'recording.mp4', 'content_type': 'video/mp4',
        'declared_bytes': bytes, 'song_id': song_id,
    })


def _direct_initiate(client, band_id, headers, *, filename='recording.mp4', bytes=10, **target):
    return client.post(f'/api/v2/bands/{band_id}/uploads/direct/initiate', headers=headers, json={
        'filename': filename, 'content_type': 'video/mp4', 'declared_bytes': bytes, **target,
    })


def _gallery_direct_initiate(client, band_id, headers, *, filename='picture.jpg', bytes=10):
    return client.post(f'/api/v2/bands/{band_id}/gallery/direct/initiate', headers=headers, json={
        'filename': filename, 'content_type': 'image/jpeg', 'declared_bytes': bytes,
    })


def test_band_initiate_requires_active_membership_scopes_keys_and_reserves_quota(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, membership_id = _song(access_app, band_id)
    with access_app.app_context():
        band = db.session.get(Band, band_id); band.storage_quota_bytes = 15; db.session.commit()
        foreign_song = Song.query.filter_by(title='B public').one().id
    calls = []
    monkeypatch.setattr(storage, 'create_multipart_upload', lambda key, content_type: calls.append(key) or 'phase8')
    endpoint = f'/api/v2/bands/{band_id}/uploads/multipart/initiate'
    assert write_client.post(endpoint, json={'filename': 'recording.mp4', 'declared_bytes': 10, 'song_id': song_id}).status_code == 401
    assert _initiate(write_client, band_id, principals['outsider'], song_id).status_code == 403
    assert _initiate(write_client, band_id, principals['member'], foreign_song).status_code == 404
    assert calls == []

    started = _initiate(write_client, band_id, principals['member'], song_id)
    assert started.status_code == 201
    payload = started.get_json()
    with access_app.app_context():
        session = db.session.get(MultipartUploadSession, payload['session_id'])
        assert session.band_id == band_id and session.created_by_membership_id == membership_id
        assert session.is_legacy is False
        assert session.object_key == f'bands/{band_id}/media/{payload["filename"]}'
    assert calls == [f'bands/{band_id}/media/{payload["filename"]}']
    # The live session itself reserves 10 bytes, so another 10-byte initiate
    # cannot oversubscribe this band's 15-byte quota.
    assert _initiate(write_client, band_id, principals['member'], song_id).status_code == 409
    assert len(calls) == 1


def test_capability_continues_parts_but_complete_rechecks_current_membership(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, membership_id = _song(access_app, band_id)
    monkeypatch.setattr(storage, 'create_multipart_upload', lambda key, content_type: 'phase8')
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    started = _initiate(write_client, band_id, principals['member'], song_id)
    assert started.status_code == 201
    session = started.get_json(); session_id = session['session_id']
    capability = {session['capability_header']: session['upload_capability_token']}
    base = f'/api/v2/bands/{band_id}/uploads/multipart/{session_id}'
    with access_app.app_context():
        membership = db.session.get(BandMembership, membership_id)
        membership.status = 'left'; db.session.commit()
    # Capability routes remain resumable and intentionally do not require the
    # former member to keep a valid login while uploading parts.
    assert write_client.post(f'{base}/parts', headers=capability, json={'part_number': 1}).status_code == 200
    assert write_client.post(f'{base}/parts/1/ack', headers=capability,
                             json={'etag': 'etag', 'bytes': 10}).status_code == 200
    assert write_client.post(f'{base}/complete', headers={**principals['member'], **capability}).status_code == 403
    with access_app.app_context():
        assert Media.query.filter_by(band_id=band_id).count() == 0
        db.session.get(BandMembership, membership_id).status = 'active'; db.session.commit()
    completed = write_client.post(f'{base}/complete', headers={**principals['member'], **capability})
    assert completed.status_code == 201
    with access_app.app_context():
        media = Media.query.filter_by(band_id=band_id).one()
        assert media.created_by_membership_id == membership_id
        assert media.object_key == f'bands/{band_id}/media/{session["filename"]}'


def test_unrecovered_aborting_session_still_reserves_band_quota(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, _membership_id = _song(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).storage_quota_bytes = 15
        db.session.commit()
    monkeypatch.setattr(storage, 'create_multipart_upload', lambda key, content_type: 'phase8')
    started = _initiate(write_client, band_id, principals['member'], song_id)
    assert started.status_code == 201
    with access_app.app_context():
        db.session.get(MultipartUploadSession, started.get_json()['session_id']).status = 'aborting'
        db.session.commit()
        monkeypatch.setattr(storage, 'abort_multipart_upload',
                            lambda *args: (_ for _ in ()).throw(RuntimeError('R2 unavailable')))
        recover_sessions()
        assert db.session.get(MultipartUploadSession, started.get_json()['session_id']).status == 'aborting'
    # The unknown R2 outcome remains reserved instead of admitting an upload
    # that could exceed the band's quota once recovery succeeds.
    assert _initiate(write_client, band_id, principals['member'], song_id).status_code == 409


def test_direct_initiate_requires_membership_scopes_key_and_reserves_quota(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    song_id, membership_id = _song(access_app, band_id)
    with access_app.app_context():
        band = db.session.get(Band, band_id)
        band.storage_quota_bytes = 15
        db.session.commit()
        foreign_song = Song.query.filter_by(title='B public').one().id
    endpoint = f'/api/v2/bands/{band_id}/uploads/direct/initiate'
    assert write_client.post(endpoint, json={'filename': 'recording.mp4', 'declared_bytes': 10,
                                             'song_id': song_id}).status_code == 401
    assert _direct_initiate(write_client, band_id, principals['outsider'], song_id=song_id).status_code == 403
    assert _direct_initiate(write_client, band_id, principals['member'], song_id=foreign_song).status_code == 404

    started = _direct_initiate(write_client, band_id, principals['member'], song_id=song_id)
    assert started.status_code == 201
    payload = started.get_json()
    assert payload['upload_url'].endswith(f'bands/{band_id}/media/{payload["filename"]}')
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, payload['session_id'])
        assert session.band_id == band_id and session.created_by_membership_id == membership_id
        assert session.object_key == f'bands/{band_id}/media/{payload["filename"]}'
        assert session.status == 'initiated'
    # The direct reservation is accounted together with the existing multipart
    # reservation pool, so a second 10-byte PUT cannot oversubscribe 15 bytes.
    assert _direct_initiate(write_client, band_id, principals['member'], song_id=song_id).status_code == 409


def test_direct_complete_requires_capability_and_current_membership_is_idempotent(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, membership_id = _song(access_app, band_id)
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    started = _direct_initiate(write_client, band_id, principals['member'], song_id=song_id)
    payload = started.get_json()
    complete = f'/api/v2/bands/{band_id}/uploads/direct/{payload["session_id"]}/complete'
    capability = {payload['capability_header']: payload['upload_capability_token']}
    assert write_client.post(complete, headers=principals['member'], json={}).status_code == 403
    with access_app.app_context():
        db.session.get(BandMembership, membership_id).status = 'left'
        db.session.commit()
    assert write_client.post(complete, headers={**principals['member'], **capability}, json={}).status_code == 403
    with access_app.app_context():
        db.session.get(BandMembership, membership_id).status = 'active'
        db.session.commit()
    completed = write_client.post(complete, headers={**principals['member'], **capability}, json={})
    assert completed.status_code == 201
    repeated = write_client.post(complete, headers={**principals['member'], **capability}, json={})
    assert repeated.status_code == 200
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, payload['session_id'])
        media = db.session.get(Media, session.media_id)
        assert session.status == 'completed' and media.band_id == band_id
        assert media.object_key == f'bands/{band_id}/media/{payload["filename"]}'
        assert Media.query.filter_by(band_id=band_id).count() == 1


def test_gallery_direct_upload_is_tenant_scoped_quota_reserved_and_idempotent(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    with access_app.app_context():
        band = db.session.get(Band, band_id)
        band.storage_quota_bytes = 15
        membership = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        membership_id = membership.id
        db.session.commit()
    endpoint = f'/api/v2/bands/{band_id}/gallery/direct/initiate'
    assert write_client.post(endpoint, json={'filename': 'picture.jpg', 'declared_bytes': 10}).status_code == 401
    assert _gallery_direct_initiate(write_client, band_id, principals['outsider']).status_code == 403

    started = _gallery_direct_initiate(write_client, band_id, principals['member'])
    assert started.status_code == 201
    payload = started.get_json()
    assert payload['upload_url'].endswith(f'bands/{band_id}/gallery/{payload["filename"]}')
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, payload['session_id'])
        assert session.kind == 'gallery'
        assert session.band_id == band_id and session.created_by_membership_id == membership_id
        assert session.result_gallery_image_id is None
    # The reservation shares the quota ledger with completed GalleryImage rows
    # and every other live direct session.
    assert _gallery_direct_initiate(write_client, band_id, principals['member']).status_code == 409

    complete = f'/api/v2/bands/{band_id}/gallery/direct/{payload["session_id"]}/complete'
    capability = {payload['capability_header']: payload['upload_capability_token']}
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    assert write_client.post(complete, headers=principals['member'], json={}).status_code == 403
    with access_app.app_context():
        db.session.get(BandMembership, membership_id).status = 'left'
        db.session.commit()
    assert write_client.post(complete, headers={**principals['member'], **capability}, json={}).status_code == 403
    with access_app.app_context():
        db.session.get(BandMembership, membership_id).status = 'active'
        db.session.commit()
    assert write_client.post(complete, headers={**principals['member'], **capability}, json={}).status_code == 201
    assert write_client.post(complete, headers={**principals['member'], **capability}, json={}).status_code == 200
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, payload['session_id'])
        image = db.session.get(GalleryImage, session.result_gallery_image_id)
        assert session.status == 'completed' and image.band_id == band_id
        assert image.created_by_membership_id == membership_id
        assert image.object_key == f'bands/{band_id}/gallery/{payload["filename"]}'
        assert GalleryImage.query.filter_by(band_id=band_id).count() == 1


def test_gallery_direct_expiry_releases_quota_and_deletes_only_the_tenant_key(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    with access_app.app_context():
        db.session.get(Band, band_id).storage_quota_bytes = 10
        db.session.commit()
    first = _gallery_direct_initiate(write_client, band_id, principals['member'])
    assert first.status_code == 201
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, first.get_json()['session_id'])
        session.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.session.commit()
        expired_key = session.object_key
    deleted = []
    monkeypatch.setattr(storage, 'delete', lambda key: deleted.append(key))
    assert _gallery_direct_initiate(write_client, band_id, principals['member']).status_code == 201
    with access_app.app_context():
        assert db.session.get(DirectUploadSession, first.get_json()['session_id']).status == 'expired'
    assert deleted == [expired_key]


def test_expired_direct_reservation_is_released_before_the_next_quota_check(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, _membership_id = _song(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).storage_quota_bytes = 10
        db.session.commit()
    first = _direct_initiate(write_client, band_id, principals['member'], song_id=song_id)
    assert first.status_code == 201
    with access_app.app_context():
        db.session.get(DirectUploadSession, first.get_json()['session_id']).expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        )
        db.session.commit()
    deleted = []
    monkeypatch.setattr(storage, 'delete', lambda key: deleted.append(key))
    second = _direct_initiate(write_client, band_id, principals['member'], song_id=song_id)
    assert second.status_code == 201
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, first.get_json()['session_id'])
        assert session.status == 'expired'
        assert deleted == [session.object_key]


def test_missing_expired_direct_object_releases_reservation_idempotently(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, _membership_id = _song(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).storage_quota_bytes = 10
        db.session.commit()
    first = _direct_initiate(write_client, band_id, principals['member'], song_id=song_id)
    with access_app.app_context():
        db.session.get(DirectUploadSession, first.get_json()['session_id']).expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        )
        db.session.commit()
    monkeypatch.setattr(storage, 'delete', lambda key: (_ for _ in ()).throw(
        ClientError({'Error': {'Code': 'NoSuchKey'}}, 'DeleteObject')
    ))
    assert _direct_initiate(write_client, band_id, principals['member'], song_id=song_id).status_code == 201
    with access_app.app_context():
        assert db.session.get(DirectUploadSession, first.get_json()['session_id']).status == 'expired'


def test_transient_expiry_cleanup_keeps_reservation_until_startup_retry_succeeds(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, _membership_id = _song(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).storage_quota_bytes = 10
        db.session.commit()
    first = _direct_initiate(write_client, band_id, principals['member'], song_id=song_id)
    with access_app.app_context():
        db.session.get(DirectUploadSession, first.get_json()['session_id']).expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        )
        db.session.commit()
    monkeypatch.setattr(storage, 'delete', lambda key: (_ for _ in ()).throw(RuntimeError('R2 unavailable')))
    assert _direct_initiate(write_client, band_id, principals['member'], song_id=song_id).status_code == 503
    with access_app.app_context():
        assert db.session.get(DirectUploadSession, first.get_json()['session_id']).status == 'expiring'
    deleted = []
    monkeypatch.setattr(storage, 'delete', lambda key: deleted.append(key))
    recover_multipart_upload_sessions(access_app)
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, first.get_json()['session_id'])
        assert session.status == 'expired' and deleted == [session.object_key]
    assert _direct_initiate(write_client, band_id, principals['member'], song_id=song_id).status_code == 201


def test_direct_complete_and_expiry_claims_allow_exactly_one_terminal_path(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, _membership_id = _song(access_app, band_id)
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    deleted = []
    monkeypatch.setattr(storage, 'delete', lambda key: deleted.append(key))

    complete_wins = _direct_initiate(write_client, band_id, principals['member'], song_id=song_id)
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, complete_wins.get_json()['session_id'])
        session.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.session.commit()
        # This models a complete request that read initiated before the expiry
        # worker claimed it: only its conditional initiated -> completing wins.
        item, status = complete_direct_session(session, lambda key: 10, _v2_direct_item)
        assert status == 201 and item.id
        assert expire_direct_session_if_needed(db.session.get(DirectUploadSession, session.id)) is False
        assert db.session.get(DirectUploadSession, session.id).status == 'completed'

    expiry_wins = _direct_initiate(write_client, band_id, principals['member'], song_id=song_id)
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, expiry_wins.get_json()['session_id'])
        session.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.session.commit()
        assert expire_direct_session_if_needed(session) is True
        assert db.session.get(DirectUploadSession, session.id).status == 'expired'
        with pytest.raises(ValidationError):
            complete_direct_session(db.session.get(DirectUploadSession, session.id), lambda key: 10, _v2_direct_item)
    assert len(deleted) == 1


def test_file_sqlite_direct_complete_and_expiry_claims_are_mutually_exclusive(tmp_path, monkeypatch):
    """Each terminal winner is exercised with an independent SQLite connection."""
    db_path = tmp_path / 'direct-complete-expiry-race.db'

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path.as_posix()}'

    app = create_app(FileConfig, start_worker=False)
    try:
        with app.app_context():
            db.drop_all(); db.create_all()
            user = AppUser(supabase_user_id='44444444-4444-4444-4444-444444444444',
                           email='direct-race@example.com')
            band = Band(name='direct race', slug='direct-race', default_visibility='PUBLIC')
            db.session.add_all((user, band)); db.session.flush()
            membership = BandMembership(band_id=band.id, user_id=user.id, role='member', status='active')
            db.session.add(membership); db.session.flush()
            db.session.add(TenantBackfillState(key='phase4_tenant_scope', status='validated'))
            song = Song(band_id=band.id, created_by_membership_id=membership.id,
                        visibility='PUBLIC', title='direct race', artist='band')
            db.session.add(song); db.session.commit()
            band_id, membership_id, song_id = band.id, membership.id, song.id

        def start_expired_session(suffix):
            with app.app_context():
                session, _ = create_direct_session(
                    band_id=band_id, created_by_membership_id=membership_id,
                    kind='media', object_key=f'bands/{band_id}/media/{suffix}.mp4',
                    original_filename=f'{suffix}.mp4', content_type='video/mp4', declared_bytes=10,
                    song_id=song_id, status='initiated',
                )
                session.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
                db.session.commit()
                return session.id

        def create_item(session, actual_size):
            item = Media(
                band_id=session.band_id, created_by_membership_id=session.created_by_membership_id,
                visibility='PUBLIC', song_id=session.song_id,
                filename=session.object_key.rsplit('/', 1)[1], original_filename=session.original_filename,
                object_key=session.object_key, file_type='video', file_size=actual_size,
            )
            db.session.add(item); db.session.flush()
            return item

        # Completion claims first, then the expiry worker observes `completing`.
        complete_id = start_expired_session('complete-wins')
        complete_claimed, release_complete = Event(), Event()

        def blocking_object_size(_key):
            complete_claimed.set()
            assert release_complete.wait(5)
            return 10

        def complete_request():
            with app.app_context():
                session = db.session.get(DirectUploadSession, complete_id)
                return complete_direct_session(session, blocking_object_size, create_item)[1]

        def expiry_request(session_id):
            with app.app_context():
                return expire_direct_session_if_needed(db.session.get(DirectUploadSession, session_id))

        with ThreadPoolExecutor(max_workers=2) as pool:
            complete_future = pool.submit(complete_request)
            assert complete_claimed.wait(5)
            expiry_future = pool.submit(expiry_request, complete_id)
            assert expiry_future.result(timeout=5) is False
            release_complete.set()
            assert complete_future.result(timeout=5) == 201
        with app.app_context():
            assert db.session.get(DirectUploadSession, complete_id).status == 'completed'
            assert Media.query.filter_by(band_id=band_id).count() == 1

        # Expiry claims first and blocks at object deletion; completion must get a 409.
        expiry_id = start_expired_session('expiry-wins')
        expiry_claimed, release_expiry = Event(), Event()
        deleted = []

        def blocking_delete(key):
            deleted.append(key)
            expiry_claimed.set()
            assert release_expiry.wait(5)

        monkeypatch.setattr(storage, 'delete', blocking_delete)

        def rejected_complete_request():
            with app.app_context():
                session = db.session.get(DirectUploadSession, expiry_id)
                try:
                    complete_direct_session(session, lambda _key: 10, create_item)
                except ValidationError as exc:
                    return exc.status_code
                return 201

        with ThreadPoolExecutor(max_workers=2) as pool:
            expiry_future = pool.submit(expiry_request, expiry_id)
            assert expiry_claimed.wait(5)
            complete_future = pool.submit(rejected_complete_request)
            assert complete_future.result(timeout=5) == 409
            release_expiry.set()
            assert expiry_future.result(timeout=5) is True
        with app.app_context():
            assert db.session.get(DirectUploadSession, expiry_id).status == 'expired'
            assert Media.query.filter_by(band_id=band_id).count() == 1
        assert deleted == [f'bands/{band_id}/media/expiry-wins.mp4']
    finally:
        with app.app_context():
            db.session.remove(); db.drop_all()


def test_file_sqlite_direct_initiates_keep_quota_lock_while_expiry_is_released(tmp_path, monkeypatch):
    """Expiry cleanup and reservation insertion are one quota transaction."""
    db_path = tmp_path / 'direct-expiry-quota-race.db'

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path.as_posix()}'

    app = create_app(FileConfig, start_worker=False)
    try:
        with app.app_context():
            db.drop_all(); db.create_all()
            user = AppUser(supabase_user_id='88888888-8888-8888-8888-888888888888',
                           email='direct-quota-race@example.com')
            band = Band(name='direct quota race', slug='direct-quota-race', default_visibility='PUBLIC',
                        storage_quota_bytes=10)
            db.session.add_all((user, band)); db.session.flush()
            membership = BandMembership(band_id=band.id, user_id=user.id, role='member', status='active')
            db.session.add(membership); db.session.flush()
            song = Song(band_id=band.id, created_by_membership_id=membership.id,
                        visibility='PUBLIC', title='quota target', artist='band')
            db.session.add(song); db.session.flush()
            expired, _ = create_direct_session(
                band_id=band.id, created_by_membership_id=membership.id, kind='media',
                object_key=f'bands/{band.id}/media/expired.mp4', original_filename='expired.mp4',
                content_type='video/mp4', declared_bytes=10, song_id=song.id, status='initiated',
            )
            expired.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
            db.session.commit()
            band_id, membership_id, song_id, expired_id, expired_key = (
                band.id, membership.id, song.id, expired.id, expired.object_key,
            )

        deleted = []
        monkeypatch.setattr(storage, 'delete', lambda key: deleted.append(key))
        first_reserved, release_first = Event(), Event()
        create_lock = Lock()
        create_count = 0
        original_create = upload_routes.create_direct_session

        def delayed_create(*args, **kwargs):
            nonlocal create_count
            with create_lock:
                create_count += 1
                is_first = create_count == 1
            if is_first:
                first_reserved.set()
                assert release_first.wait(5)
            return original_create(*args, **kwargs)

        monkeypatch.setattr(upload_routes, 'create_direct_session', delayed_create)

        def initiate():
            with app.app_context():
                def operation():
                    band = db.session.get(Band, band_id)
                    used, reserved = upload_routes._v2_used_and_reserved_bytes(band_id)
                    if used + reserved + 10 > band.storage_quota_bytes:
                        raise ValidationError('Band storage quota exceeded.', status_code=413)
                    return upload_routes.create_direct_session(
                        band_id=band_id, created_by_membership_id=membership_id, kind='media',
                        object_key=f'bands/{band_id}/media/new-{threading.get_ident()}.mp4',
                        original_filename='new.mp4', content_type='video/mp4', declared_bytes=10,
                        song_id=song_id, status='initiated', commit=False,
                    )
                try:
                    upload_routes._v2_write(operation)
                    return 201
                except ValidationError as exc:
                    return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(initiate)
            assert first_reserved.wait(5)
            second = pool.submit(initiate)
            release_first.set()
            results = {first.result(timeout=5), second.result(timeout=5)}

        assert results == {201, 413}
        assert create_count == 1
        assert deleted == [expired_key]
        with app.app_context():
            assert db.session.get(DirectUploadSession, expired_id).status == 'expired'
            assert DirectUploadSession.query.filter_by(band_id=band_id, status='initiated').count() == 1
    finally:
        with app.app_context():
            db.session.remove(); db.drop_all()


def test_file_sqlite_transient_expiry_cleanup_aborts_each_initiate_without_overquota(tmp_path, monkeypatch):
    """A failed delete never lets a released SQLite lock continue an initiate."""
    db_path = tmp_path / 'direct-expiry-cleanup-pending-race.db'

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path.as_posix()}'

    app = create_app(FileConfig, start_worker=False)
    try:
        with app.app_context():
            db.drop_all(); db.create_all()
            user = AppUser(supabase_user_id='99999999-9999-9999-9999-999999999999',
                           email='direct-cleanup-pending@example.com')
            band = Band(name='direct cleanup pending', slug='direct-cleanup-pending',
                        default_visibility='PUBLIC', storage_quota_bytes=25)
            db.session.add_all((user, band)); db.session.flush()
            membership = BandMembership(band_id=band.id, user_id=user.id, role='member', status='active')
            db.session.add(membership); db.session.flush()
            song = Song(band_id=band.id, created_by_membership_id=membership.id,
                        visibility='PUBLIC', title='cleanup target', artist='band')
            db.session.add(song); db.session.flush()
            expired, _ = create_direct_session(
                band_id=band.id, created_by_membership_id=membership.id, kind='media',
                object_key=f'bands/{band.id}/media/expired.mp4', original_filename='expired.mp4',
                content_type='video/mp4', declared_bytes=10, song_id=song.id, status='initiated',
            )
            expired.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
            db.session.commit()
            band_id, membership_id, song_id, expired_id = band.id, membership.id, song.id, expired.id

        monkeypatch.setattr(storage, 'delete', lambda _key: (_ for _ in ()).throw(RuntimeError('R2 unavailable')))
        start = Barrier(2)

        def initiate():
            with app.app_context():
                start.wait(timeout=5)
                def operation():
                    band = db.session.get(Band, band_id)
                    used, reserved = upload_routes._v2_used_and_reserved_bytes(band_id)
                    if used + reserved + 10 > band.storage_quota_bytes:
                        raise ValidationError('Band storage quota exceeded.', status_code=413)
                    return upload_routes.create_direct_session(
                        band_id=band_id, created_by_membership_id=membership_id, kind='media',
                        object_key=f'bands/{band_id}/media/new-{threading.get_ident()}.mp4',
                        original_filename='new.mp4', content_type='video/mp4', declared_bytes=10,
                        song_id=song_id, status='initiated', commit=False,
                    )
                try:
                    upload_routes._v2_direct_write(operation)
                    return 201
                except ValidationError as exc:
                    return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(initiate) for _ in range(2)]
            results = [future.result(timeout=5) for future in futures]

        assert results.count(503) >= 1
        assert results.count(201) <= 1
        with app.app_context():
            expired = db.session.get(DirectUploadSession, expired_id)
            reserved = sum(session.declared_bytes for session in DirectUploadSession.query.filter(
                DirectUploadSession.band_id == band_id,
                DirectUploadSession.status.in_(('initiated', 'completing', 'expiring')),
            ))
            assert expired.status == 'expiring'
            assert DirectUploadSession.query.filter_by(band_id=band_id, status='initiated').count() == results.count(201)
            assert reserved <= db.session.get(Band, band_id).storage_quota_bytes
    finally:
        with app.app_context():
            db.session.remove(); db.drop_all()


def test_startup_recovers_interrupted_direct_completion_without_touching_the_object(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, _membership_id = _song(access_app, band_id)
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    started = _direct_initiate(write_client, band_id, principals['member'], song_id=song_id)
    payload = started.get_json()
    with access_app.app_context():
        db.session.get(DirectUploadSession, payload['session_id']).status = 'completing'
        db.session.commit()
    recover_multipart_upload_sessions(access_app)
    with access_app.app_context():
        assert db.session.get(DirectUploadSession, payload['session_id']).status == 'initiated'
    complete = f'/api/v2/bands/{band_id}/uploads/direct/{payload["session_id"]}/complete'
    headers = {**principals['member'], payload['capability_header']: payload['upload_capability_token']}
    assert write_client.post(complete, headers=headers, json={}).status_code == 201
    # A later crash after record creation is terminalized from its stored ID,
    # not by repeating object work or creating a duplicate media row.
    with access_app.app_context():
        db.session.get(DirectUploadSession, payload['session_id']).status = 'completing'
        db.session.commit()
    recover_multipart_upload_sessions(access_app)
    with access_app.app_context():
        assert db.session.get(DirectUploadSession, payload['session_id']).status == 'completed'
        assert Media.query.filter_by(band_id=band_id).count() == 1


def test_direct_personal_logs_isolate_member_scope_and_completed_gallery_counts_toward_quota(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    with access_app.app_context():
        membership = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        member = Member(band_id=band_id, name='direct log member', instrument='drums')
        gallery = GalleryImage(band_id=band_id, created_by_membership_id=membership.id,
                               visibility='MEMBERS', filename='existing.jpg', file_size=6)
        db.session.add_all((member, gallery))
        db.session.flush()
        foreign_member = Member(band_id=Band.query.filter_by(slug='band-b').one().id,
                                name='foreign member', instrument='bass')
        db.session.add(foreign_member)
        db.session.get(Band, band_id).storage_quota_bytes = 15
        db.session.commit()
        member_id, foreign_member_id = member.id, foreign_member.id
    # 6 completed gallery bytes are part of the same band quota, leaving only 9.
    assert _direct_initiate(write_client, band_id, principals['member'], bytes=10,
                            member_id=member_id).status_code == 409
    assert _direct_initiate(write_client, band_id, principals['member'], filename='take.m4a', bytes=8,
                            member_id=foreign_member_id).status_code == 404
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 8})
    started = _direct_initiate(write_client, band_id, principals['member'], filename='take.m4a', bytes=8,
                               member_id=member_id, title='Take')
    assert started.status_code == 201
    payload = started.get_json()
    complete = f'/api/v2/bands/{band_id}/uploads/direct/{payload["session_id"]}/complete'
    completed = write_client.post(complete, headers={**principals['member'], payload['capability_header']:
                                                      payload['upload_capability_token']}, json={})
    assert completed.status_code == 201
    with access_app.app_context():
        log = db.session.get(PersonalLog, db.session.get(DirectUploadSession, payload['session_id']).personal_log_id)
        assert log.member_id == member_id and log.object_key == f'bands/{band_id}/personal_logs/{payload["filename"]}'
