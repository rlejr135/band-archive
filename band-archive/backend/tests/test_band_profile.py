"""Owner-only band introduction profile contracts."""

import threading

import pytest
from flask import request

from app import create_app
from band_service import band_dict
from extensions import db
from models import AppUser, AuditEvent, Band, BandMembership
from tests.test_auth import AuthTestBootstrapConfig, _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import access_app, signer


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


def _token_for(signer, subject, email):
    return _headers(_token(signer, sub=subject, email=email))


@pytest.fixture
def profile_client(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        bands = {band.slug: band.id for band in Band.query.all()}
        users = {user.email: user.id for user in AppUser.query.all()}
    principals = {
        'owner_a': _token_for(signer, '11111111-1111-1111-1111-111111111111', 'user@example.com'),
        'admin_a': _token_for(signer, '22222222-2222-2222-2222-222222222222', 'admin@example.com'),
        'member_a': _token_for(signer, '33333333-3333-3333-3333-333333333333', 'member@example.com'),
        'pending': _token_for(signer, '44444444-4444-4444-4444-444444444444', 'pending@example.com'),
        'rejected': _token_for(signer, '55555555-5555-5555-5555-555555555555', 'rejected@example.com'),
        'left': _token_for(signer, '66666666-6666-6666-6666-666666666666', 'left@example.com'),
        'owner_b': _token_for(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com'),
    }
    return {
        'app': access_app,
        'client': access_app.test_client(),
        'bands': bands,
        'users': users,
        'principals': principals,
    }


def test_profile_patch_requires_authenticated_active_owner_in_target_band(profile_client):
    app, client = profile_client['app'], profile_client['client']
    band_a, band_b = profile_client['bands']['band-a'], profile_client['bands']['band-b']
    endpoint = f'/api/v2/bands/{band_a}/profile'
    assert client.patch(endpoint, json={'description': 'x'}).status_code == 401

    for principal in ('admin_a', 'member_a'):
        response = client.patch(endpoint, headers=profile_client['principals'][principal],
                                json={'description': 'not allowed'})
        assert response.status_code == 403
    for principal in ('pending', 'rejected', 'left', 'owner_b'):
        response = client.patch(endpoint, headers=profile_client['principals'][principal],
                                json={'description': 'not allowed'})
        assert response.status_code == 404

    # Each owner is confined to the band where their active owner row exists.
    assert client.patch(f'/api/v2/bands/{band_b}/profile',
                        headers=profile_client['principals']['owner_a'],
                        json={'description': 'wrong band'}).status_code == 404
    assert client.patch(f'/api/v2/bands/999999/profile',
                        headers=profile_client['principals']['owner_a'],
                        json={'description': 'missing'}).status_code == 404

    with app.app_context():
        outsider = AppUser.query.filter_by(email='outsider@example.com').one()
        outsider.is_platform_admin = True
        db.session.commit()
    assert client.patch(endpoint, headers=profile_client['principals']['owner_b'],
                        json={'description': 'platform admin does not bypass'}).status_code == 404
    with app.app_context():
        assert db.session.get(Band, band_a).description is None
        assert db.session.get(Band, band_b).description is None


def test_profile_patch_trims_description_and_audits_only_changed_field(profile_client):
    app, client = profile_client['app'], profile_client['client']
    band_id = profile_client['bands']['band-a']
    response = client.patch(f'/api/v2/bands/{band_id}/profile',
                            headers=profile_client['principals']['owner_a'],
                            json={'description': '  A plain-text introduction  '})
    assert response.status_code == 200
    band = response.get_json()['band']
    assert band['description'] == 'A plain-text introduction'
    assert band['permissions']['can_edit_band_profile'] is True
    assert set(response.get_json()) == {'band'}

    with app.app_context():
        audit = AuditEvent.query.filter_by(event_type='band_profile_updated').one()
        assert audit.band_id == band_id
        assert audit.actor_user_id == profile_client['users']['user@example.com']
        assert audit.details_json == '{"fields": ["description"]}'
        assert 'A plain-text introduction' not in audit.details_json
        assert AuditEvent.query.filter_by(event_type='band_profile_updated').count() == 1


def test_profile_permissions_are_owner_only_and_description_can_be_cleared(profile_client):
    app, client = profile_client['app'], profile_client['client']
    with app.app_context():
        band_a = Band.query.filter_by(slug='band-a').one()
        owner = AppUser.query.filter_by(email='user@example.com').one()
        admin = AppUser.query.filter_by(email='admin@example.com').one()
        member = AppUser.query.filter_by(email='member@example.com').one()
        assert band_dict(band_a, owner)['permissions']['can_edit_band_profile'] is True
        assert band_dict(band_a, admin)['permissions']['can_edit_band_profile'] is False
        assert band_dict(band_a, member)['permissions']['can_edit_band_profile'] is False

    endpoint = f"/api/v2/bands/{profile_client['bands']['band-a']}/profile"
    owner_headers = profile_client['principals']['owner_a']
    assert client.patch(endpoint, headers=owner_headers, json={'description': 'remove me'}).status_code == 200
    cleared = client.patch(endpoint, headers=owner_headers, json={'description': ' \t '})
    assert cleared.status_code == 200
    assert cleared.get_json()['band']['description'] is None
    with app.app_context():
        assert Band.query.filter_by(slug='band-a').one().description is None


def test_inactive_account_cannot_edit_even_with_an_owner_membership(profile_client):
    app, client = profile_client['app'], profile_client['client']
    band_id = profile_client['bands']['band-a']
    with app.app_context():
        owner = AppUser.query.filter_by(email='user@example.com').one()
        owner.status = 'disabled'
        db.session.commit()
    response = client.patch(
        f'/api/v2/bands/{band_id}/profile',
        headers=profile_client['principals']['owner_a'],
        json={'description': 'disabled account'},
    )
    assert response.status_code == 403
    with app.app_context():
        assert db.session.get(Band, band_id).description is None
        assert AuditEvent.query.filter_by(event_type='band_profile_updated').count() == 0


@pytest.mark.parametrize('payload', [
    {},
    {'description': None},
    {'description': 123},
    {'description': ['text']},
    {'description': 'valid', 'name': 'not permitted'},
])
def test_profile_patch_rejects_missing_null_nonstring_and_unknown_fields(profile_client, payload):
    band_id = profile_client['bands']['band-a']
    response = profile_client['client'].patch(
        f'/api/v2/bands/{band_id}/profile',
        headers=profile_client['principals']['owner_a'], json=payload,
    )
    assert response.status_code == 400
    with profile_client['app'].app_context():
        assert db.session.get(Band, band_id).description is None
        assert AuditEvent.query.filter_by(event_type='band_profile_updated').count() == 0


def test_profile_patch_enforces_trimmed_2000_character_limit(profile_client):
    band_id = profile_client['bands']['band-a']
    endpoint = f'/api/v2/bands/{band_id}/profile'
    owner_headers = profile_client['principals']['owner_a']
    accepted = profile_client['client'].patch(endpoint, headers=owner_headers,
                                               json={'description': 'x' * 2000})
    assert accepted.status_code == 200
    assert len(accepted.get_json()['band']['description']) == 2000
    rejected = profile_client['client'].patch(endpoint, headers=owner_headers,
                                               json={'description': 'x' * 2001})
    assert rejected.status_code == 400
    with profile_client['app'].app_context():
        assert len(db.session.get(Band, band_id).description) == 2000


def test_profile_patch_rolls_back_description_when_audit_fails(profile_client, monkeypatch):
    import band_service

    def fail_audit(*args, **kwargs):
        raise RuntimeError('injected audit failure')

    monkeypatch.setattr(band_service, 'audit', fail_audit)
    band_id = profile_client['bands']['band-a']
    with pytest.raises(RuntimeError, match='injected audit failure'):
        profile_client['client'].patch(
            f'/api/v2/bands/{band_id}/profile',
            headers=profile_client['principals']['owner_a'],
            json={'description': 'must roll back'},
        )
    with profile_client['app'].app_context():
        assert db.session.get(Band, band_id).description is None
        assert AuditEvent.query.filter_by(event_type='band_profile_updated').count() == 0


def test_sqlite_profile_update_serializes_with_concurrent_ownership_transfer(tmp_path, signer, monkeypatch):
    """An update queued behind the transfer must recheck current owner authority."""
    db_path = tmp_path / 'band-profile-transfer-race.db'

    class FileAuthConfig(AuthTestBootstrapConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path.as_posix()}'

    app = create_app(FileAuthConfig, start_worker=False)
    try:
        with app.app_context():
            db.create_all()
            owner = AppUser(supabase_user_id='11111111-1111-1111-1111-111111111111',
                            email='owner@example.com')
            admin = AppUser(supabase_user_id='22222222-2222-2222-2222-222222222222',
                            email='admin@example.com', email_verified=True)
            band = Band(name='Race', slug='profile-race', description='original')
            db.session.add_all((owner, admin, band))
            db.session.flush()
            owner_membership = BandMembership(band_id=band.id, user_id=owner.id,
                                               role='owner', status='active')
            admin_membership = BandMembership(band_id=band.id, user_id=admin.id,
                                               role='admin', status='active')
            db.session.add_all((owner_membership, admin_membership))
            db.session.commit()
            owner_id, band_id, target_membership_id = owner.id, band.id, admin_membership.id
            app.config['BAND_API_SCHEMA_READY'] = True
        app.config['SUPABASE_AUTH_ENABLED'] = True
        _install_jwks(app, [[_jwk(signer, 'key-1')]])
        owner_headers = _token_for(signer, '11111111-1111-1111-1111-111111111111', 'owner@example.com')

        import band_service

        real_begin = band_service._begin_privileged_mutation
        transfer_holds_lock = threading.Event()
        allow_transfer = threading.Event()
        profile_request_started = threading.Event()

        def ordered_begin():
            current_name = threading.current_thread().name
            if current_name == 'ownership-transfer':
                real_begin()
                transfer_holds_lock.set()
                assert allow_transfer.wait(20)
                return
            real_begin()

        monkeypatch.setattr(band_service, '_begin_privileged_mutation', ordered_begin)
        transfer_result = []
        profile_result = []

        @app.before_request
        def mark_profile_request_started():
            if threading.current_thread().name == 'profile-update' and request.path.endswith('/profile'):
                profile_request_started.set()

        def transfer():
            with app.app_context():
                try:
                    actor = db.session.get(AppUser, owner_id)
                    from band_service import transfer_ownership
                    transfer_ownership(actor, band_id, target_membership_id)
                    transfer_result.append('transferred')
                except Exception as error:
                    transfer_result.append(error)
                finally:
                    db.session.remove()

        def update_profile():
            with app.test_client() as client:
                response = client.patch(f'/api/v2/bands/{band_id}/profile',
                                        headers=owner_headers,
                                        json={'description': 'must not follow transfer'})
                profile_result.append(response.status_code)

        transfer_thread = threading.Thread(target=transfer, name='ownership-transfer')
        transfer_thread.start()
        assert transfer_holds_lock.wait(5)
        profile_thread = threading.Thread(target=update_profile, name='profile-update')
        profile_thread.start()
        assert profile_request_started.wait(5)
        allow_transfer.set()
        transfer_thread.join(10)
        profile_thread.join(10)
        assert not transfer_thread.is_alive() and not profile_thread.is_alive()
        assert transfer_result == ['transferred']
        assert profile_result == [403]

        with app.app_context():
            assert db.session.get(Band, band_id).description == 'original'
            assert BandMembership.query.filter_by(
                band_id=band_id, user_id=owner_id, status='active', role='owner',
            ).count() == 0
            assert AuditEvent.query.filter_by(event_type='band_profile_updated').count() == 0
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
