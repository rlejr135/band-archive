import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import create_app
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
    SUPABASE_JWKS_CACHE_SECONDS = 300
    SUPABASE_HTTP_TIMEOUT_SECONDS = 1
    SUPABASE_JWT_LEEWAY_SECONDS = 0
    BOOTSTRAP_PLATFORM_ADMIN_EMAIL = 'Admin@Example.com'


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


@pytest.fixture
def auth_app():
    app = create_app(AuthConfig, start_worker=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
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


def test_me_rejects_forged_and_unknown_kid_tokens(auth_app, signer):
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    assert _get_me(auth_app, _token(forged)).status_code == 401
    assert _get_me(auth_app, _token(signer, kid='unknown')).status_code == 401


def test_jwks_unknown_kid_refreshes_for_rotation(auth_app, signer):
    next_signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls = _install_jwks(auth_app, [[_jwk(signer, 'key-1')], [_jwk(next_signer, 'key-2')]])
    assert _get_me(auth_app, _token(signer, kid='key-1')).status_code == 200
    assert _get_me(auth_app, _token(next_signer, kid='key-2')).status_code == 200
    assert len(calls) == 2


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
    assert auth_app.test_client().get('/songs').status_code == 200
    with auth_app.app_context():
        assert AppUser.query.count() == 1


def test_no_server_logout_session_and_expiry_remains_authoritative(auth_app, signer):
    _install_jwks(auth_app, [[_jwk(signer, 'key-1')]])
    valid = _token(signer)
    assert _get_me(auth_app, valid).status_code == 200
    assert _get_me(auth_app, valid).status_code == 200
    expired = _token(signer, exp=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert _get_me(auth_app, expired).status_code == 401
