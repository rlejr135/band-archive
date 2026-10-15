"""Phase 6 read-scope contracts for the central band access boundary."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import create_app
from band_service import band_dict
from errors import NotFoundError, ValidationError
from extensions import db
from models import AppUser, Band, BandMembership, Song
from tests.test_auth import AuthTestBootstrapConfig, _install_jwks, _jwk, _token


@pytest.fixture
def signer():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def access_app():
    app = create_app(AuthTestBootstrapConfig, start_worker=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
        users = [
            AppUser(supabase_user_id='11111111-1111-1111-1111-111111111111',
                    email='user@example.com'),
            AppUser(supabase_user_id='22222222-2222-2222-2222-222222222222',
                    email='admin@example.com'),
            AppUser(supabase_user_id='33333333-3333-3333-3333-333333333333',
                    email='member@example.com'),
            AppUser(supabase_user_id='44444444-4444-4444-4444-444444444444',
                    email='pending@example.com'),
            AppUser(supabase_user_id='55555555-5555-5555-5555-555555555555',
                    email='rejected@example.com'),
            AppUser(supabase_user_id='66666666-6666-6666-6666-666666666666',
                    email='left@example.com'),
            AppUser(supabase_user_id='77777777-7777-7777-7777-777777777777',
                    email='outsider@example.com'),
        ]
        db.session.add_all(users)
        db.session.flush()
        private = Band(name='Band A', slug='band-a', default_visibility='MEMBERS')
        public = Band(name='Band B', slug='band-b', default_visibility='PUBLIC')
        db.session.add_all((private, public))
        db.session.flush()
        db.session.add_all((
            BandMembership(band_id=private.id, user_id=users[0].id, role='owner', status='active'),
            BandMembership(band_id=private.id, user_id=users[1].id, role='admin', status='active'),
            BandMembership(band_id=private.id, user_id=users[2].id, role='member', status='active'),
            BandMembership(band_id=private.id, user_id=users[3].id, role='member', status='pending'),
            BandMembership(band_id=private.id, user_id=users[4].id, role='member', status='rejected'),
            BandMembership(band_id=private.id, user_id=users[5].id, role='member', status='left'),
            BandMembership(band_id=public.id, user_id=users[6].id, role='owner', status='active'),
            BandMembership(band_id=public.id, user_id=users[3].id, role='member', status='pending'),
            BandMembership(band_id=public.id, user_id=users[4].id, role='member', status='rejected'),
            BandMembership(band_id=public.id, user_id=users[5].id, role='member', status='left'),
        ))
        db.session.flush()
        owner_membership = BandMembership.query.filter_by(band_id=private.id, user_id=users[0].id).one()
        member_membership = BandMembership.query.filter_by(band_id=private.id, user_id=users[2].id).one()
        db.session.add_all((
            Song(band_id=private.id, created_by_membership_id=owner_membership.id,
                 visibility='PUBLIC', title='A public', artist='A'),
            Song(band_id=private.id, created_by_membership_id=member_membership.id,
                 visibility='MEMBERS', title='A members', artist='A'),
            Song(band_id=private.id, visibility='PUBLIC', title='A legacy', artist='A'),
            Song(band_id=public.id, visibility='PUBLIC', title='B public', artist='B'),
        ))
        db.session.commit()
        app.config['BAND_API_SCHEMA_READY'] = True
    app.config['SUPABASE_AUTH_ENABLED'] = True
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


def _scope(app, token=None, slug='band-a'):
    from band_access import current_audience, read_scope_for_slug

    headers = _headers(token) if token else {}
    with app.test_request_context(headers=headers):
        return read_scope_for_slug(slug, current_audience())


def test_optional_auth_treats_missing_header_as_visitor_but_never_downgrades_invalid_bearer(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    client = access_app.test_client()

    visitor = client.get('/api/v2/bands/by-slug/band-b')
    assert visitor.status_code == 200
    assert visitor.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in visitor.headers['Vary']
    concealed = client.get('/api/v2/bands/by-slug/band-a')
    assert concealed.status_code == 404
    assert concealed.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in concealed.headers['Vary']

    valid = _token(signer)
    authenticated = client.get('/api/v2/bands/by-slug/band-a', headers=_headers(valid))
    assert authenticated.status_code == 200
    assert authenticated.get_json()['band']['membership']['role'] == 'owner'
    assert authenticated.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in authenticated.headers['Vary']

    invalid_headers = (
        {'Authorization': 'Basic abc'},
        {'Authorization': 'Bearer '},
        {'Authorization': 'Bearer not-a-jwt'},
        _headers(_token(signer, exp=datetime.now(timezone.utc) - timedelta(seconds=1))),
    )
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    invalid_headers += (
        _headers(_token(forged)),
        _headers(_token(signer, kid='unknown-kid')),
    )
    for headers in invalid_headers:
        response = client.get('/api/v2/bands/by-slug/band-b', headers=headers)
        assert response.status_code == 401
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']


def test_public_band_never_serializes_nonactive_memberships(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    client = access_app.test_client()
    for subject, email in (
        ('44444444-4444-4444-4444-444444444444', 'pending@example.com'),
        ('55555555-5555-5555-5555-555555555555', 'rejected@example.com'),
        ('66666666-6666-6666-6666-666666666666', 'left@example.com'),
    ):
        response = client.get('/api/v2/bands/by-slug/band-b', headers=_headers(_token(
            signer, sub=subject, email=email,
        )))
        assert response.status_code == 200
        band = response.get_json()['band']
        assert band['membership'] is None
        assert band['can_manage'] is False
        assert band['can_edit'] is False
        assert band['can_delete'] is False
        assert band['permissions']['can_manage_members'] is False


def test_band_dict_explicit_none_never_looks_up_inactive_membership(access_app):
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-b').one()
        active = AppUser.query.filter_by(email='outsider@example.com').one()
        for email in ('pending@example.com', 'rejected@example.com', 'left@example.com'):
            user = AppUser.query.filter_by(email=email).one()
            explicit_none = band_dict(band, viewer=user, membership=None)
            omitted = band_dict(band, viewer=user)
            assert explicit_none['membership'] is None
            assert omitted['membership'] is None
            assert explicit_none['can_manage'] is False
        # Omitted legacy calls retain their active-member response shape.
        assert band_dict(band, viewer=active)['membership']['role'] == 'owner'


def test_audience_cache_is_bound_to_request_object_not_reused_id(access_app, signer, monkeypatch):
    import band_access
    from flask import g

    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    member_headers = _headers(_token(signer))
    # This forces the precise cache-key collision the old implementation used
    # to treat as the same request. The current code never consults this name.
    monkeypatch.setattr(band_access, 'id', lambda _: 1, raising=False)

    with access_app.app_context():
        with access_app.test_request_context(headers=member_headers):
            assert band_access.current_audience().authenticated is True
        with access_app.test_request_context():
            assert band_access.current_audience().authenticated is False

        # Reset only the old g-based cache attributes to independently verify
        # the inverse order inside the same outer application context.
        g.__dict__.pop('band_request_audience', None)
        g.__dict__.pop('band_request_audience_request_id', None)
        with access_app.test_request_context():
            assert band_access.current_audience().authenticated is False
        with access_app.test_request_context(headers=member_headers):
            assert band_access.current_audience().authenticated is True


def test_authenticated_audience_and_active_membership_roles(access_app, signer):
    from band_access import current_audience

    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    roles = {
        '11111111-1111-1111-1111-111111111111': 'owner',
        '22222222-2222-2222-2222-222222222222': 'admin',
        '33333333-3333-3333-3333-333333333333': 'member',
    }
    for subject, role in roles.items():
        token = _token(signer, sub=subject, email=f'{role}@example.com')
        scope = _scope(access_app, token)
        assert scope.audience.authenticated is True
        assert scope.membership.role == role
        assert scope.is_member is True


def test_pending_rejected_left_and_visitor_cannot_discover_private_band(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    nonmembers = (
        ('44444444-4444-4444-4444-444444444444', 'pending@example.com'),
        ('55555555-5555-5555-5555-555555555555', 'rejected@example.com'),
        ('66666666-6666-6666-6666-666666666666', 'left@example.com'),
        ('77777777-7777-7777-7777-777777777777', 'outsider@example.com'),
    )
    for subject, email in nonmembers:
        with pytest.raises(NotFoundError):
            _scope(access_app, _token(signer, sub=subject, email=email))
    with pytest.raises(NotFoundError):
        _scope(access_app)


def test_visible_query_and_scoped_lookup_are_tenant_bound_and_hide_cross_band_objects(access_app, signer):
    from band_access import current_audience, read_scope_for_slug, visible_object_or_404, visible_query

    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        with access_app.test_request_context():
            visitor_scope = read_scope_for_slug('band-b', current_audience())
            titles = [item.title for item in visible_query(Song.query, Song, visitor_scope).all()]
            assert titles == ['B public']
            # A public child cannot make a private parent visible to visitors.
            assert visible_query(Song.query, Song, visitor_scope, parent_visibility='MEMBERS').count() == 0
            with pytest.raises(NotFoundError):
                visible_object_or_404(Song, Song.query.filter_by(title='B public').one().id,
                                      visitor_scope, parent_visibility='MEMBERS')

        member_token = _token(signer, sub='33333333-3333-3333-3333-333333333333', email='member@example.com')
        with access_app.test_request_context(headers=_headers(member_token)):
            member_scope = read_scope_for_slug('band-a', current_audience())
            titles = {item.title for item in visible_query(Song.query, Song, member_scope).all()}
            assert titles == {'A public', 'A members', 'A legacy'}
            b_object = Song.query.filter_by(title='B public').one()
            with pytest.raises(NotFoundError):
                visible_object_or_404(Song, b_object.id, member_scope)

        with access_app.test_request_context():
            public_scope = read_scope_for_slug('band-b', current_audience())
            assert visible_object_or_404(Song, Song.query.filter_by(title='B public').one().id, public_scope).title == 'B public'


def test_content_capabilities_and_effective_visibility_are_server_computed(access_app, signer):
    from band_access import content_capabilities, effective_visibility, require_operation_role

    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    owner_scope = _scope(access_app, _token(signer))
    admin_scope = _scope(access_app, _token(signer, sub='22222222-2222-2222-2222-222222222222',
                                             email='admin@example.com'))
    member_scope = _scope(access_app, _token(signer, sub='33333333-3333-3333-3333-333333333333',
                                              email='member@example.com'))
    member_creator = member_scope.membership.id
    owner_creator = owner_scope.membership.id

    assert content_capabilities(owner_scope, SimpleNamespace(created_by_membership_id=member_creator)) == {
        'can_edit': True, 'can_delete': True, 'can_manage': True,
    }
    assert content_capabilities(admin_scope, SimpleNamespace(created_by_membership_id=member_creator)) == {
        'can_edit': True, 'can_delete': False, 'can_manage': True,
    }
    assert content_capabilities(member_scope, SimpleNamespace(created_by_membership_id=member_creator)) == {
        'can_edit': True, 'can_delete': False, 'can_manage': False,
    }
    assert content_capabilities(member_scope, SimpleNamespace(created_by_membership_id=owner_creator)) == {
        'can_edit': False, 'can_delete': False, 'can_manage': False,
    }
    assert content_capabilities(member_scope, SimpleNamespace(created_by_membership_id=None)) == {
        'can_edit': False, 'can_delete': False, 'can_manage': False,
    }
    assert content_capabilities(member_scope, SimpleNamespace(
        band_id=member_scope.band_id + 1000, created_by_membership_id=member_creator,
    )) == {'can_edit': False, 'can_delete': False, 'can_manage': False}

    assert effective_visibility('PUBLIC', None, 'PUBLIC') == 'PUBLIC'
    assert effective_visibility('PUBLIC', 'MEMBERS') == 'MEMBERS'
    assert effective_visibility('MEMBERS', 'PUBLIC') == 'MEMBERS'
    with pytest.raises(ValidationError) as denied:
        require_operation_role(member_scope, {'owner', 'admin'})
    assert denied.value.status_code == 403
    with pytest.raises(NotFoundError):
        require_operation_role(_scope(access_app, slug='band-b'), {'owner', 'admin'})
