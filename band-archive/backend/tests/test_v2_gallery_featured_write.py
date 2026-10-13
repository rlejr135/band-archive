"""Authenticated manager controls for the v2 featured gallery image."""

import pytest
from sqlalchemy import text

from extensions import db
from models import AppUser, AuditEvent, Band, BandMembership, GalleryImage
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


def _token_for(signer, subject, email):
    return _headers(_token(signer, sub=subject, email=email))


@pytest.fixture
def gallery_graph(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        band_a = Band.query.filter_by(slug='band-a').one()
        band_b = Band.query.filter_by(slug='band-b').one()
        owner_a = BandMembership.query.filter_by(band_id=band_a.id, role='owner').one()
        owner_b = BandMembership.query.filter_by(band_id=band_b.id, role='owner').one()
        selected = GalleryImage(
            band_id=band_a.id, created_by_membership_id=owner_a.id, visibility='MEMBERS',
            filename='selected.jpg', object_key='bands/a/gallery/selected.jpg', file_size=12,
        )
        other = GalleryImage(
            band_id=band_a.id, created_by_membership_id=owner_a.id, visibility='PUBLIC',
            filename='other.jpg', object_key='bands/a/gallery/other.jpg', file_size=13,
        )
        foreign = GalleryImage(
            band_id=band_b.id, created_by_membership_id=owner_b.id, visibility='PUBLIC',
            filename='foreign.jpg', object_key='bands/b/gallery/foreign.jpg', is_featured=True,
        )
        db.session.add_all((selected, other, foreign))
        db.session.commit()
        return {'band': band_a.id, 'other_band': band_b.id, 'selected': selected.id,
                'other': other.id, 'foreign': foreign.id, 'foreign_creator': owner_b.id}


@pytest.fixture
def principals(signer):
    return {
        'owner': _token_for(signer, '11111111-1111-1111-1111-111111111111', 'user@example.com'),
        'admin': _token_for(signer, '22222222-2222-2222-2222-222222222222', 'admin@example.com'),
        'member': _token_for(signer, '33333333-3333-3333-3333-333333333333', 'member@example.com'),
        'pending': _token_for(signer, '44444444-4444-4444-4444-444444444444', 'pending@example.com'),
        'rejected': _token_for(signer, '55555555-5555-5555-5555-555555555555', 'rejected@example.com'),
        'left': _token_for(signer, '66666666-6666-6666-6666-666666666666', 'left@example.com'),
        'outsider': _token_for(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com'),
    }


def _path(graph, image_id=None, band_id=None):
    return f"/api/v2/bands/{band_id or graph['band']}/gallery/{image_id or graph['selected']}/featured"


def test_featured_write_requires_active_manager_and_hides_foreign_targets(
        access_app, gallery_graph, principals):
    client = access_app.test_client()
    path = _path(gallery_graph)
    assert client.patch(path, json={'is_featured': True}).status_code == 401
    for role in ('member', 'pending', 'rejected', 'left', 'outsider'):
        assert client.patch(path, headers=principals[role], json={'is_featured': True}).status_code == 403

    # Platform administration does not replace active membership in this band.
    with access_app.app_context():
        outsider = AppUser.query.filter_by(email='outsider@example.com').one()
        outsider.is_platform_admin = True
        db.session.commit()
    assert client.patch(path, headers=principals['outsider'], json={'is_featured': True}).status_code == 403

    wrong_tenant = client.patch(_path(gallery_graph, gallery_graph['foreign']),
                                headers=principals['owner'], json={'is_featured': True})
    assert wrong_tenant.status_code == 404
    with access_app.app_context():
        assert db.session.get(GalleryImage, gallery_graph['selected']).is_featured is False
        assert db.session.get(GalleryImage, gallery_graph['foreign']).is_featured is True


def test_featured_switch_clear_audit_idempotence_and_member_ttl(
        access_app, gallery_graph, principals, monkeypatch):
    from storage import storage
    signed = []
    monkeypatch.setattr(storage, 'generate_url',
                        lambda key, expires_in=None: signed.append((key, expires_in)) or f'url:{key}')
    client = access_app.test_client()

    first = client.patch(_path(gallery_graph), headers=principals['admin'], json={'is_featured': True})
    assert first.status_code == 200
    assert first.get_json()['gallery']['is_featured'] is True
    assert first.get_json()['gallery']['url'] == 'url:bands/a/gallery/selected.jpg'
    assert signed[-1] == ('bands/a/gallery/selected.jpg', 300)

    second = client.patch(_path(gallery_graph, gallery_graph['other']),
                          headers=principals['owner'], json={'is_featured': True})
    assert second.status_code == 200 and second.get_json()['gallery']['is_featured'] is True
    with access_app.app_context():
        assert db.session.get(GalleryImage, gallery_graph['selected']).is_featured is False
        assert db.session.get(GalleryImage, gallery_graph['other']).is_featured is True
        assert db.session.get(GalleryImage, gallery_graph['foreign']).is_featured is True
        audit_count = AuditEvent.query.filter_by(event_type='content.gallery.featured.changed').count()
        assert audit_count == 2

    cleared = client.patch(_path(gallery_graph, gallery_graph['other']),
                           headers=principals['owner'], json={'is_featured': False})
    assert cleared.status_code == 200 and cleared.get_json()['gallery']['is_featured'] is False
    repeat = client.patch(_path(gallery_graph, gallery_graph['other']),
                          headers=principals['owner'], json={'is_featured': False})
    assert repeat.status_code == 200
    with access_app.app_context():
        assert AuditEvent.query.filter_by(event_type='content.gallery.featured.changed').count() == 3


@pytest.mark.parametrize('body', [None, {}, {'is_featured': 1}, {'is_featured': 'true'},
                                  {'is_featured': None}, {'is_featured': True, 'password': 'ignored'}])
def test_featured_write_rejects_malformed_or_extra_payload(
        access_app, gallery_graph, principals, body):
    response = access_app.test_client().patch(
        _path(gallery_graph), headers=principals['owner'], json=body,
    )
    assert response.status_code == 400


def test_featured_write_rejects_corrupt_creator_before_signing_or_mutation(
        access_app, gallery_graph, principals, monkeypatch):
    from storage import storage
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda *args, **kwargs: calls.append(args) or 'signed')
    with access_app.app_context():
        image = db.session.get(GalleryImage, gallery_graph['selected'])
        image.created_by_membership_id = gallery_graph['foreign_creator']
        db.session.commit()
    response = access_app.test_client().patch(
        _path(gallery_graph), headers=principals['owner'], json={'is_featured': True},
    )
    assert response.status_code == 404
    assert calls == []
    with access_app.app_context():
        assert db.session.get(GalleryImage, gallery_graph['selected']).is_featured is False


def test_explicit_manager_selection_heals_same_band_featured_duplicates(
        access_app, gallery_graph, principals):
    with access_app.app_context():
        # Isolated schema corruption simulation; production keeps this index.
        db.session.execute(text('DROP INDEX uq_gallery_image_featured_band'))
        selected = db.session.get(GalleryImage, gallery_graph['selected'])
        duplicate = db.session.get(GalleryImage, gallery_graph['other'])
        selected.is_featured = True
        duplicate.is_featured = True
        db.session.commit()
    response = access_app.test_client().patch(
        _path(gallery_graph, gallery_graph['other']), headers=principals['owner'],
        json={'is_featured': True},
    )
    assert response.status_code == 200
    with access_app.app_context():
        assert GalleryImage.query.filter_by(band_id=gallery_graph['band'], is_featured=True).all() == [
            db.session.get(GalleryImage, gallery_graph['other']),
        ]
