"""Isolated contracts for the private, durable R2 deletion queue."""
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
import json

from app import create_app
from deletion_worker import process_task
from deletion_service import create_or_get_task
from extensions import db
from models import (AppUser, Band, BandMembership, DirectUploadSession, Media,
                    Member, R2DeletionTask, Song, TenantBackfillState, Announcement, AuditEvent)
from storage import storage
from tests.test_auth import AuthTestBootstrapConfig, _install_jwks, _jwk, _token


OWNER_SUB = '11111111-1111-1111-1111-111111111111'
MEMBER_SUB = '33333333-3333-3333-3333-333333333333'


@pytest.fixture
def signer():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def deletion_app():
    app = create_app(AuthTestBootstrapConfig, start_worker=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
        users = [
            AppUser(supabase_user_id=OWNER_SUB, email='user@example.com'),
            AppUser(supabase_user_id='22222222-2222-2222-2222-222222222222', email='admin@example.com'),
            AppUser(supabase_user_id=MEMBER_SUB, email='member@example.com'),
            AppUser(supabase_user_id='77777777-7777-7777-7777-777777777777', email='operator@example.com'),
        ]
        db.session.add_all(users)
        db.session.flush()
        a = Band(name='Band A', slug='band-a', default_visibility='MEMBERS')
        b = Band(name='Band B', slug='band-b', default_visibility='PUBLIC')
        db.session.add_all((a, b))
        db.session.flush()
        db.session.add_all((
            BandMembership(band_id=a.id, user_id=users[0].id, role='owner', status='active'),
            BandMembership(band_id=a.id, user_id=users[1].id, role='admin', status='active'),
            BandMembership(band_id=a.id, user_id=users[2].id, role='member', status='active'),
            BandMembership(band_id=b.id, user_id=users[3].id, role='owner', status='active'),
        ))
        db.session.add(TenantBackfillState(key='phase4_tenant_scope', status='validated'))
        song = Song(band_id=a.id, title='One', artist='Band', visibility='PUBLIC')
        other_song = Song(band_id=b.id, title='Two', artist='Band', visibility='PUBLIC')
        db.session.add_all((song, other_song))
        db.session.commit()
        app.config['BAND_API_SCHEMA_READY'] = True
    app.config['SUPABASE_AUTH_ENABLED'] = True
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _media_fixture(app, *, processing='not_required', key=None):
    with app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        owner = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        membership = BandMembership.query.filter_by(band_id=band.id, user_id=owner.id).one()
        song = Song.query.filter_by(band_id=band.id).first()
        item = Media(
            band_id=band.id, created_by_membership_id=membership.id,
            song_id=song.id, filename='clip.mp4', original_filename='clip.mp4',
            object_key=key or f'bands/{band.id}/media/clip.mp4', file_type='video',
            transcoding_status=processing,
        )
        db.session.add(item)
        db.session.commit()
        return band.id, song.id, item.id, item.object_key


def _owner_headers(signer):
    return {'Authorization': f'Bearer {_token(signer)}'}


def test_owner_delete_is_idempotent_tombstones_reads_and_clears_manifest(deletion_app, signer, monkeypatch):
    _install_jwks(deletion_app, [[_jwk(signer, 'key-1')]])
    deletion_app.config['PG_R2_DELETION_WORKER_ENABLED'] = True
    band_id, song_id, media_id, key = _media_fixture(deletion_app)
    client = deletion_app.test_client()
    deleted = client.delete(f'/api/v2/bands/{band_id}/media/{media_id}', headers=_owner_headers(signer))
    assert deleted.status_code == 202
    task_id = deleted.get_json()['task_id']
    assert deleted.get_json()['status'] == 'queued'
    assert deleted.get_json()['resource_type'] == 'media'

    duplicate = client.delete(f'/api/v2/bands/{band_id}/media/{media_id}', headers=_owner_headers(signer))
    assert duplicate.status_code == 202
    assert duplicate.get_json()['task_id'] == task_id

    hidden = client.get(f'/api/v2/bands/{band_id}/songs/{song_id}/media', headers=_owner_headers(signer))
    assert hidden.status_code == 200
    assert hidden.get_json()['media'] == []

    deleted_keys = []
    monkeypatch.setattr(storage, 'delete_bounded', lambda value: deleted_keys.append(value))
    with deletion_app.app_context():
        task = db.session.get(R2DeletionTask, task_id)
        owner = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        membership = BandMembership.query.filter_by(band_id=band_id, user_id=owner.id).one()
        completed_upload = DirectUploadSession(
            band_id=band_id, created_by_membership_id=membership.id, kind='media',
            object_key=key, original_filename='clip.mp4', content_type='video/mp4',
            declared_bytes=1024, song_id=song_id, status='completed',
            capability_token_hash='test-only-hash', media_id=media_id,
        )
        db.session.add(completed_upload)
        db.session.flush()
        completed_upload_id = completed_upload.id
        task.status = 'processing'
        task.lease_token = 'test-lease'
        from deletion_service import utcnow
        from datetime import timedelta
        task.lease_expires_at = utcnow() + timedelta(seconds=120)
        db.session.commit()
        assert process_task(task_id, 'test-lease') is True
        completed = db.session.get(R2DeletionTask, task_id)
        assert completed.status == 'completed'
        assert completed.object_keys is None
        assert completed.actor_user_id is None
        assert db.session.get(Media, media_id) is None
        assert db.session.get(DirectUploadSession, completed_upload_id).media_id is None
    assert deleted_keys == [key]


def test_active_member_cannot_retrieve_or_reuse_owner_task(deletion_app, signer):
    _install_jwks(deletion_app, [[_jwk(signer, 'key-1')]])
    deletion_app.config['PG_R2_DELETION_WORKER_ENABLED'] = True
    band_id, _song_id, media_id, _key = _media_fixture(deletion_app)
    client = deletion_app.test_client()
    first = client.delete(f'/api/v2/bands/{band_id}/media/{media_id}', headers=_owner_headers(signer))
    assert first.status_code == 202
    member_token = _token(signer, sub=MEMBER_SUB, email='member@example.com')
    response = client.delete(
        f'/api/v2/bands/{band_id}/media/{media_id}',
        headers={'Authorization': f'Bearer {member_token}'},
    )
    assert response.status_code == 403


def test_media_processing_and_shared_cross_band_keys_fail_closed(deletion_app, signer, monkeypatch):
    _install_jwks(deletion_app, [[_jwk(signer, 'key-1')]])
    deletion_app.config['PG_R2_DELETION_WORKER_ENABLED'] = True
    band_id, _song_id, media_id, key = _media_fixture(deletion_app, processing='processing')
    client = deletion_app.test_client()
    response = client.delete(f'/api/v2/bands/{band_id}/media/{media_id}', headers=_owner_headers(signer))
    assert response.status_code == 409
    with deletion_app.app_context():
        assert R2DeletionTask.query.count() == 0
        media = db.session.get(Media, media_id)
        second_band = Band.query.filter_by(slug='band-b').one()
        other_song = Song.query.filter_by(band_id=second_band.id).first()
        second_owner = BandMembership.query.filter_by(band_id=second_band.id).one()
        db.session.add(Media(
            band_id=second_band.id, song_id=other_song.id, filename='other.mp4',
            object_key=key, file_type='video', transcoding_status='not_required',
            created_by_membership_id=second_owner.id,
        ))
        media.transcoding_status = 'not_required'
        db.session.commit()
    conflict = client.delete(f'/api/v2/bands/{band_id}/media/{media_id}', headers=_owner_headers(signer))
    assert conflict.status_code == 409
    with deletion_app.app_context():
        assert R2DeletionTask.query.count() == 0


def test_platform_announcement_clear_is_synchronous_and_audits_reason_atomically(deletion_app, signer):
    _install_jwks(deletion_app, [[_jwk(signer, 'key-1')]])
    deletion_app.config['PG_R2_DELETION_WORKER_ENABLED'] = False
    with deletion_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        operator = AppUser.query.filter_by(supabase_user_id='77777777-7777-7777-7777-777777777777').one()
        operator.is_platform_admin = True
        owner = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        membership = BandMembership.query.filter_by(band_id=band.id, user_id=owner.id).one()
        announcement = Announcement(band_id=band.id, created_by_membership_id=membership.id,
                                    visibility='PUBLIC', content='private text')
        db.session.add(announcement)
        db.session.commit()
        band_id, announcement_id = band.id, announcement.id
    token = _token(signer, sub='77777777-7777-7777-7777-777777777777', email='operator@example.com')
    client = deletion_app.test_client()
    response = client.post(
        f'/api/v2/platform/bands/{band_id}/deletions',
        headers={'Authorization': f'Bearer {token}'},
        json={'resource_type': 'announcement', 'resource_id': announcement_id,
              'reason': 'User-requested content cleanup', 'confirm': True},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data == {'status': 'completed', 'resource_type': 'announcement', 'resource_id': announcement_id}
    with deletion_app.app_context():
        event = AuditEvent.query.filter_by(event_type='platform.content.deletion.completed').one()
        assert json.loads(event.details_json)['reason'] == 'User-requested content cleanup'
        assert R2DeletionTask.query.count() == 0
        announcement = db.session.get(Announcement, announcement_id)
        assert announcement is not None and announcement.content == ''


def test_owner_member_delete_is_synchronous_and_cleans_terminal_upload_session(deletion_app, signer):
    _install_jwks(deletion_app, [[_jwk(signer, 'key-1')]])
    deletion_app.config['PG_R2_DELETION_WORKER_ENABLED'] = False
    with deletion_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        # Exercise the isolated-metadata test path for this legacy-only model.
        db.session.get(TenantBackfillState, 'phase4_tenant_scope').status = 'failed'
        owner = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        membership = BandMembership.query.filter_by(band_id=band.id, user_id=owner.id).one()
        member = Member(band_id=band.id, name='No files', instrument='voice')
        db.session.add(member)
        db.session.flush()
        session = DirectUploadSession(
            band_id=band.id, created_by_membership_id=membership.id, kind='personal_log',
            object_key=f'bands/{band.id}/personal_logs/expired.mp3',
            original_filename='expired.mp3', content_type='audio/mpeg', declared_bytes=1024,
            member_id=member.id, status='expired', capability_token_hash='test-only-hash',
        )
        db.session.add(session)
        db.session.flush()
        member_id, band_id = member.id, band.id
        session_id = session.id
        db.session.commit()
    response = deletion_app.test_client().delete(
        f'/api/v2/bands/{band_id}/members/{member_id}', headers=_owner_headers(signer),
    )
    assert response.status_code == 200
    with deletion_app.app_context():
        assert db.session.get(Member, member_id) is None
        assert db.session.get(DirectUploadSession, session_id) is None


def test_legacy_file_redirect_does_not_sign_pending_media(deletion_app, signer, monkeypatch):
    band_id, _song_id, media_id, _key = _media_fixture(deletion_app)
    with deletion_app.app_context():
        band = db.session.get(Band, band_id)
        band.is_legacy_import = True
        media = db.session.get(Media, media_id)
        media.object_key = 'media/clip.mp4'
        owner = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        task, created = create_or_get_task(band, owner, 'media', media)
        assert created is True
        db.session.commit()
    signed = []
    monkeypatch.setattr(storage, 'generate_url', lambda key: signed.append(key) or 'https://example.invalid')
    response = deletion_app.test_client().get('/uploads/clip.mp4')
    assert response.status_code == 404
    assert signed == []
