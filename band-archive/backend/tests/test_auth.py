import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

from app import create_app
import auth_service
from auth_service import JWKSCache
from config import TestingConfig
from extensions import db
from models import AppUser, AuthBootstrapState


class AuthConfig(TestingConfig):
    SUPABASE_AUTH_ENABLED = True
    SUPABASE_URL = 'https://project.supabase.co'
    SUPABASE_PROJECT_REF = 'project'
    SUPABASE_JWT_ISSUER = 'https://project.supabase.co/auth/v1'
    SUPABASE_JWT_AUDIENCE = 'authenticated'
    SUPABASE_JWKS_URL = 'https://project.supabase.co/auth/v1/.well-known/jwks.json'
    SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_test'
    SUPABASE_JWKS_CACHE_SECONDS = 300
    SUPABASE_HTTP_TIMEOUT_SECONDS = 1
    SUPABASE_JWT_LEEWAY_SECONDS = 0
    BOOTSTRAP_PLATFORM_ADMIN_EMAIL = 'Admin@Example.com'


class AuthTestBootstrapConfig(AuthConfig):
    """Tests create the managed schema explicitly after app startup."""
    SUPABASE_AUTH_ENABLED = False


def _jwk(private_key, kid):
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    data.update({'kid': kid, 'alg': 'RS256', 'use': 'sig'})
    return data


class _Response:
    def __init__(self, keys):
        self.keys = keys

    def raise_for_status(self):
        return None

    def json(self):
        return {'keys': self.keys}


class _UserResponse:
    def __init__(self, user, status_code=200):
        self.user = user
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f'HTTP {self.status_code}')
            error.response = self
            raise error
        return None

    def json(self):
        return self.user


@pytest.fixture
def auth_app():
    app = create_app(AuthTestBootstrapConfig, start_worker=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
    app.config['SUPABASE_AUTH_ENABLED'] = True
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def signer():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(private_key, kid='key-1', **overrides):
    now = datetime.now(timezone.utc)
    claims = {
        'sub': '11111111-1111-1111-1111-111111111111',
        'email': 'User@Example.com',
        'email_verified': True,
        'iss': AuthConfig.SUPABASE_JWT_ISSUER,
        'aud': AuthConfig.SUPABASE_JWT_AUDIENCE,
        'iat': now,
        'nbf': now,
        'exp': now + timedelta(minutes=5),
    }
    claims.update(overrides)
    if claims.pop('_without_nbf', False):
        claims.pop('nbf')
    if claims.pop('_without_email_verified', False):
        claims.pop('email_verified')
    return jwt.encode(claims, private_key, algorithm='RS256', headers={'kid': kid})


def _install_jwks(app, responses):
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        return _Response(responses[min(len(calls) - 1, len(responses) - 1)])

    app.extensions['supabase_jwks_cache'] = JWKSCache(
        app.config['SUPABASE_JWKS_URL'], 300, 1, fetch=fetch
    )
    return calls


def _get_me(app, token=None):
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    return app.test_client().get('/api/v2/me', headers=headers)


def test_band_slug_availability_uses_verified_supabase_auth_and_synchronizes_user(auth_app, signer):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    auth_app.config['BAND_API_SCHEMA_READY'] = True
    client = auth_app.test_client()
    endpoint = '/api/v2/band-slugs/availability?slug=general-band'

    unauthenticated = client.get(endpoint)
    assert unauthenticated.status_code == 401
    assert unauthenticated.get_json()['error'] == 'missing_token'

    authenticated = client.get(endpoint, headers={'Authorization': f'Bearer {_token(signer)}'})
    assert authenticated.status_code == 200
    assert authenticated.get_json() == {'slug': 'general-band', 'available': True}
    with auth_app.app_context():
        user = AppUser.query.filter_by(
            supabase_user_id='11111111-1111-1111-1111-111111111111',
        ).one()
        assert user.email == 'user@example.com' and user.email_verified is True


def test_me_requires_well_formed_bearer_header(auth_app):
    assert _get_me(auth_app).get_json()['error'] == 'missing_token'
    response = auth_app.test_client().get('/api/v2/me', headers={'Authorization': 'Basic x'})
    assert response.status_code == 401
    assert response.get_json()['error'] == 'malformed_token'


@pytest.mark.parametrize('claim,value', [
    ('exp', datetime.now(timezone.utc) - timedelta(seconds=1)),
    ('iss', 'https://wrong.example/auth/v1'),
    ('aud', 'wrong-audience'),
    ('email_verified', False),
])
def test_me_rejects_invalid_or_unverified_claims(auth_app, signer, claim, value):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    response = _get_me(auth_app, _token(signer, **{claim: value}))
    assert response.status_code == 401


def test_me_confirms_standard_supabase_token_via_trusted_user_endpoint(auth_app, signer, monkeypatch):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    calls = []

    def fetch(url, headers, timeout, allow_redirects):
        calls.append((url, headers, timeout, allow_redirects))
        return _UserResponse({
            'id': '11111111-1111-1111-1111-111111111111',
            'email': 'user@example.com',
            'email_confirmed_at': '2026-10-06T00:00:00Z',
        })

    monkeypatch.setattr(auth_service.requests, 'get', fetch)
    response = _get_me(auth_app, _token(signer, _without_email_verified=True))
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] == 'https://project.supabase.co/auth/v1/user'
    assert calls[0][1]['Authorization'].startswith('Bearer ')
    assert calls[0][1]['apikey'] == 'sb_publishable_test'
    assert calls[0][3] is False


def test_me_rejects_standard_token_when_user_endpoint_has_no_email_confirmation(auth_app, signer, monkeypatch):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])

    monkeypatch.setattr(auth_service.requests, 'get', lambda *args, **kwargs: _UserResponse({
        'id': '11111111-1111-1111-1111-111111111111',
        'email': 'user@example.com',
        'email_confirmed_at': None,
    }))
    response = _get_me(auth_app, _token(signer, _without_email_verified=True))
    assert response.status_code == 401
    assert response.get_json()['error'] == 'email_not_verified'


@pytest.mark.parametrize('field,value', [
    ('id', '22222222-2222-2222-2222-222222222222'),
    ('email', 'other@example.com'),
])
def test_me_rejects_standard_token_when_user_endpoint_identity_does_not_match(auth_app, signer, monkeypatch, field, value):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    user = {
        'id': '11111111-1111-1111-1111-111111111111',
        'email': 'user@example.com',
        'email_confirmed_at': '2026-10-06T00:00:00Z',
    }
    user[field] = value
    monkeypatch.setattr(auth_service.requests, 'get', lambda *args, **kwargs: _UserResponse(user))
    response = _get_me(auth_app, _token(signer, _without_email_verified=True))
    assert response.status_code == 401
    assert response.get_json()['error'] == 'invalid_token'


def test_me_fails_closed_when_user_endpoint_redirects_or_is_malformed(auth_app, signer, monkeypatch):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    monkeypatch.setattr(auth_service.requests, 'get', lambda *args, **kwargs: _UserResponse({}, status_code=302))
    redirected = _get_me(auth_app, _token(signer, _without_email_verified=True))
    assert redirected.status_code == 503
    monkeypatch.setattr(auth_service.requests, 'get', lambda *args, **kwargs: _UserResponse(['not-a-user']))
    malformed = _get_me(auth_app, _token(signer, _without_email_verified=True))
    assert malformed.status_code == 503


def test_me_fails_closed_on_user_endpoint_timeout(auth_app, signer, monkeypatch):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    monkeypatch.setattr(auth_service.requests, 'get', lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout()))
    response = _get_me(auth_app, _token(signer, _without_email_verified=True))
    assert response.status_code == 503


def test_explicit_unverified_claim_wins_over_timestamp(auth_app, signer):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    response = _get_me(auth_app, _token(
        signer, email_verified=False, email_confirmed_at='2026-10-06T00:00:00Z',
    ))
    assert response.status_code == 401
    assert response.get_json()['error'] == 'email_not_verified'


def test_me_rejects_forged_and_unknown_kid_tokens(auth_app, signer):
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls = _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    assert _get_me(auth_app, _token(signer)).status_code == 200
    for _ in range(4):
        assert _get_me(auth_app, _token(forged)).status_code == 401
    # One bounded same-kid rotation lookup is allowed; invalid signatures do
    # not turn into one network fetch per bearer-token request.
    assert len(calls) == 2

    calls = _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    assert _get_me(auth_app, _token(signer)).status_code == 200
    for _ in range(4):
        assert _get_me(auth_app, _token(signer, kid='unknown')).status_code == 401
    assert len(calls) == 2


def test_jwks_unknown_kid_refreshes_for_rotation(auth_app, signer):
    next_signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls = _install_jwks(auth_app, [[_jwk(signer, 'key-1')], [_jwk(next_signer, 'key-2')]])
    assert _get_me(auth_app, _token(signer, kid='key-1')).status_code == 200
    assert _get_me(auth_app, _token(next_signer, kid='key-2')).status_code == 200
    assert len(calls) == 2


def test_jwks_unknown_kid_refresh_is_single_flight_under_concurrency(signer):
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        return _Response([_jwk(signer, 'key-1')])

    cache = JWKSCache('https://project.supabase.co/auth/v1/.well-known/jwks.json', 300, 1, fetch=fetch)
    cache.key_for('key-1')  # prime a known, fresh cache generation

    def unknown_key():
        with pytest.raises(Exception):
            cache.key_for('unknown')

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: unknown_key(), range(8)))

    # Initial fill plus exactly one serialized rotation probe.
    assert len(calls) == 2


def test_optional_nbf_is_accepted_but_future_nbf_is_rejected(auth_app, signer):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    assert _get_me(auth_app, _token(signer, _without_nbf=True)).status_code == 200
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert _get_me(auth_app, _token(signer, nbf=future)).status_code == 401


def test_me_synchronizes_normalized_user_and_bootstraps_admin_once(auth_app, signer):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    first = _get_me(auth_app, _token(signer, email='ADMIN@example.com'))
    assert first.status_code == 200
    assert first.get_json()['user']['email'] == 'admin@example.com'
    assert first.get_json()['user']['is_platform_admin'] is True

    second = _get_me(auth_app, _token(
        signer, sub='22222222-2222-2222-2222-222222222222', email='admin2@example.com'
    ))
    assert second.status_code == 200
    assert second.get_json()['user']['is_platform_admin'] is False
    with auth_app.app_context():
        assert AuthBootstrapState.query.count() == 1


def test_duplicate_normalized_email_is_rejected_and_v1_stays_public(auth_app, signer):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    assert _get_me(auth_app, _token(signer, email='Person@Example.com')).status_code == 200
    conflict = _get_me(auth_app, _token(
        signer, sub='33333333-3333-3333-3333-333333333333', email='person@example.com'
    ))
    assert conflict.status_code == 409
    assert conflict.get_json()['error'] == 'identity_conflict'
    # v1 remains unauthenticated, but no longer operates against an
    # unproven tenant when this isolated auth fixture has no backfill marker.
    assert auth_app.test_client().get('/songs').status_code == 503
    with auth_app.app_context():
        assert AppUser.query.count() == 1


def test_no_server_logout_session_and_expiry_remains_authoritative(auth_app, signer):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    valid = _token(signer)
    assert _get_me(auth_app, valid).status_code == 200
    assert _get_me(auth_app, valid).status_code == 200
    expired = _token(signer, exp=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert _get_me(auth_app, expired).status_code == 401
