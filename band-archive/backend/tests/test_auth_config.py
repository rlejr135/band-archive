import pytest

from app import create_app
from config import TestingConfig


class AuthEnabledConfig(TestingConfig):
    SUPABASE_AUTH_ENABLED = True
    SUPABASE_URL = 'https://example.supabase.co'
    SUPABASE_PROJECT_REF = 'example'
    SUPABASE_JWT_ISSUER = 'https://example.supabase.co/auth/v1'
    SUPABASE_JWT_AUDIENCE = 'authenticated'
    SUPABASE_JWKS_URL = 'https://example.supabase.co/auth/v1/.well-known/jwks.json'
    SUPABASE_JWKS_CACHE_SECONDS = 60
    SUPABASE_HTTP_TIMEOUT_SECONDS = 1
    SUPABASE_JWT_LEEWAY_SECONDS = 0


def test_v1_compatibility_keeps_auth_disabled_by_default(app):
    assert app.config['SUPABASE_AUTH_ENABLED'] is False


def test_enabled_auth_fails_fast_without_complete_contract():
    class IncompleteAuthConfig(AuthEnabledConfig):
        SUPABASE_JWKS_URL = ''

    with pytest.raises(RuntimeError, match='SUPABASE_JWKS_URL'):
        create_app(IncompleteAuthConfig, start_worker=False)


def test_enabled_auth_accepts_complete_non_secret_contract():
    app = create_app(AuthEnabledConfig, start_worker=False)
    assert app.config['SUPABASE_JWT_AUDIENCE'] == 'authenticated'
