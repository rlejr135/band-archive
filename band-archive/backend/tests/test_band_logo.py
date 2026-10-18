from datetime import datetime, timezone

from extensions import db
from models import AppUser, Band, BandLogo, BandMembership, GalleryImage, R2DeletionTask
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import _headers, access_app, signer


def test_band_logo_owner_selection_is_same_band_and_visible_in_band_payload(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-b').one()
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        image = GalleryImage(band_id=band.id, created_by_membership_id=owner.id,
                             visibility='PUBLIC', filename='logo.jpg',
                             object_key='bands/b/logo.jpg')
        private_band = Band.query.filter_by(slug='band-a').one()
        foreign_image = GalleryImage(band_id=private_band.id, visibility='PUBLIC',
                                      filename='foreign.jpg', object_key='bands/a/foreign.jpg')
        db.session.add_all((image, foreign_image))
        db.session.commit()
        image_id, band_id, foreign_image_id = image.id, band.id, foreign_image.id
    owner_token = _token(signer, sub='77777777-7777-7777-7777-777777777777', email='outsider@example.com')
    client = access_app.test_client()

    selected = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': image_id},
                          headers=_headers(owner_token))
    assert selected.status_code == 200
    assert selected.get_json()['logo'] == {
        'gallery_image_id': image_id, 'url': 'https://storage.test/bands/b/logo.jpg',
    }
    assert selected.headers['Cache-Control'] == 'private, no-store'
    cross_band = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': foreign_image_id},
                            headers=_headers(owner_token))
    assert cross_band.status_code == 404
    read = client.get('/api/v2/bands/by-slug/band-b')
    payload = read.get_json()['band']
    assert payload['logo'] == {'gallery_image_id': image_id, 'url': 'https://storage.test/bands/b/logo.jpg'}
    assert payload['permissions']['can_manage_logo'] is False
    assert read.headers['Cache-Control'] == 'private, no-store'
    management = client.get('/api/v2/bands/by-slug/band-b/logo-management', headers=_headers(owner_token))
    assert management.status_code == 200
    data = management.get_json()
    assert data['permissions'] == {'can_manage_logo': True}
    assert data['logo'] == payload['logo']
    assert data['gallery'] == [{'id': image_id, 'url': 'https://storage.test/bands/b/logo.jpg',
                                'is_featured': False}]
    assert 'object_key' not in str(data)

    member_token = _token(signer, sub='33333333-3333-3333-3333-333333333333', email='member@example.com')
    denied = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': None},
                        headers=_headers(member_token))
    assert denied.status_code == 404
    rejected_bool = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': True},
                               headers=_headers(owner_token))
    assert rejected_bool.status_code == 400
    assert client.get('/api/v2/bands/by-slug/band-b/logo-management').status_code == 404


def test_logo_fail_closed_for_pending_or_private_image_and_clear_is_idempotent(access_app, signer, monkeypatch):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        image = GalleryImage(band_id=band.id, created_by_membership_id=owner.id,
                             visibility='MEMBERS', filename='private.jpg', object_key='bands/a/private.jpg')
        db.session.add(image)
        db.session.commit()
        image_id, band_id = image.id, band.id
    owner_token = _token(signer)
    client = access_app.test_client()
    from storage import storage
    signed_ttls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, expires_in=None:
                        signed_ttls.append(expires_in) or f'https://storage.test/{key}')
    picked = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': image_id},
                        headers=_headers(owner_token))
    assert picked.status_code == 200
    assert signed_ttls == [300]
    assert picked.get_json()['logo']['gallery_image_id'] == image_id
    assert client.get('/api/v2/bands/by-slug/band-a', headers=_headers(owner_token)).get_json()['band']['logo'][
        'gallery_image_id'] == image_id

    # A signer outage preserves the selected id for management, while public
    # representations fail closed and never leak the raw storage key.
    monkeypatch.setattr(storage, 'generate_url', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('offline')))
    result = client.get('/api/v2/bands/by-slug/band-a', headers=_headers(owner_token)).get_json()['band']['logo']
    assert result is None
    private_owner_management = client.get('/api/v2/bands/by-slug/band-a/logo-management',
                                          headers=_headers(owner_token)).get_json()
    assert private_owner_management['logo'] == {'gallery_image_id': image_id, 'url': None}

    with access_app.app_context():
        db.session.add(R2DeletionTask(
            band_id=band_id, resource_type='gallery', resource_id=image_id,
            object_keys=['bands/a/private.jpg'], status='queued',
        ))
        db.session.commit()
    pending_put = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': image_id},
                             headers=_headers(owner_token))
    assert pending_put.status_code == 404
    pending_management = client.get('/api/v2/bands/by-slug/band-a/logo-management',
                                    headers=_headers(owner_token)).get_json()
    assert pending_management['logo'] is None
    assert pending_management['gallery'] == []

    clear = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': None},
                       headers=_headers(owner_token))
    assert clear.status_code == 200 and clear.get_json() == {'logo': None}
    again = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': None},
                       headers=_headers(owner_token))
    assert again.status_code == 200 and again.get_json() == {'logo': None}
    with access_app.app_context():
        assert BandLogo.query.filter_by(band_id=band_id).one_or_none() is None


def test_logo_is_owner_only_for_same_band_and_rejects_inactive_owner(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    owner_token = _token(signer)
    client = access_app.test_client()
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        image = GalleryImage(band_id=band.id, created_by_membership_id=owner.id,
                             visibility='PUBLIC', filename='logo.jpg', object_key='bands/a/logo.jpg')
        deleted = Band(name='Deleted logo band', slug='deleted-logo-band',
                       default_visibility='PUBLIC', deleted_at=datetime.now(timezone.utc))
        db.session.add_all((image, deleted))
        db.session.commit()
        image_id, band_id, deleted_id = image.id, band.id, deleted.id

    for subject, email in (
        ('22222222-2222-2222-2222-222222222222', 'admin@example.com'),
        ('33333333-3333-3333-3333-333333333333', 'member@example.com'),
    ):
        token = _token(signer, sub=subject, email=email)
        assert client.get('/api/v2/bands/by-slug/band-a/logo-management',
                          headers=_headers(token)).status_code == 404
        assert client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': image_id},
                          headers=_headers(token)).status_code == 404
        read = client.get('/api/v2/bands/by-slug/band-a', headers=_headers(token))
        assert read.status_code == 200
        assert read.get_json()['band']['permissions']['can_manage_logo'] is False

    assert client.get('/api/v2/bands/by-slug/deleted-logo-band/logo-management',
                      headers=_headers(owner_token)).status_code == 404
    assert client.put(f'/api/v2/bands/{deleted_id}/logo', json={'gallery_image_id': image_id},
                      headers=_headers(owner_token)).status_code == 404

    with access_app.app_context():
        owner_user = BandMembership.query.filter_by(band_id=band_id, role='owner').one().user
        owner_user.status = 'disabled'
        db.session.commit()
    inactive = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': image_id},
                          headers=_headers(owner_token))
    assert inactive.status_code in {401, 403, 404}
    with access_app.app_context():
        assert BandLogo.query.filter_by(band_id=band_id).one_or_none() is None


def test_logo_platform_admin_is_narrow_and_management_is_not_normal_private_read(access_app, signer, monkeypatch):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        superadmin = AppUser(supabase_user_id='88888888-8888-8888-8888-888888888888',
                             email='superadmin@example.com', email_verified=True, is_platform_admin=True)
        unverified_admin = AppUser(supabase_user_id='99999999-9999-9999-9999-999999999999',
                                   email='unverified-admin@example.com', email_verified=False,
                                   is_platform_admin=True)
        db.session.add_all((superadmin, unverified_admin))
        band = Band.query.filter_by(slug='band-a').one()
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        image = GalleryImage(band_id=band.id, created_by_membership_id=owner.id,
                             visibility='MEMBERS', filename='private.jpg', object_key='bands/a/private.jpg')
        db.session.add(image)
        db.session.commit()
        image_id, band_id = image.id, band.id
    admin_token = _token(signer, sub='88888888-8888-8888-8888-888888888888', email='superadmin@example.com')
    client = access_app.test_client()
    assert client.get('/api/v2/bands/by-slug/band-a').status_code == 404
    signed_ttls = []
    from storage import storage
    monkeypatch.setattr(storage, 'generate_url', lambda key, expires_in=None:
                        signed_ttls.append(expires_in) or f'https://storage.test/{key}')
    management = client.get('/api/v2/bands/by-slug/band-a/logo-management', headers=_headers(admin_token))
    assert management.status_code == 200
    assert management.get_json()['gallery'] == [{'id': image_id,
        'url': 'https://storage.test/bands/a/private.jpg', 'is_featured': False}]
    assert signed_ttls == [300]
    selected = client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': image_id},
                          headers=_headers(admin_token))
    assert selected.status_code == 200
    assert client.get('/api/v2/bands/by-slug/band-a').status_code == 404
    unverified_token = _token(signer, sub='99999999-9999-9999-9999-999999999999',
                              email='unverified-admin@example.com', email_verified=False)
    assert client.get('/api/v2/bands/by-slug/band-a/logo-management',
                      headers=_headers(unverified_token)).status_code == 401
    assert client.put(f'/api/v2/bands/{band_id}/logo', json={'gallery_image_id': image_id},
                      headers=_headers(unverified_token)).status_code == 401
