import pytest

from app import create_app
from config import TestingConfig, validate_supabase_auth_config


class AuthEnabledConfig(TestingConfig):
    SUPABASE_AUTH_ENABLED = True
    SUPABASE_URL = 'https://example.supabase.co'
    SUPABASE_PROJECT_REF = 'example'
    SUPABASE_JWT_ISSUER = 'https://example.supabase.co/auth/v1'
    SUPABASE_JWT_AUDIENCE = 'authenticated'
    SUPABASE_JWKS_URL = 'https://example.supabase.co/auth/v1/.well-known/jwks.json'
    SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_test'
    SUPABASE_JWKS_CACHE_SECONDS = 60
    SUPABASE_HTTP_TIMEOUT_SECONDS = 1
    SUPABASE_JWT_LEEWAY_SECONDS = 0
    BOOTSTRAP_PLATFORM_ADMIN_EMAIL = 'Admin@Example.com'


def test_v1_compatibility_keeps_auth_disabled_by_default(app):
    assert app.config['SUPABASE_AUTH_ENABLED'] is False


def test_enabled_auth_fails_fast_without_complete_contract():
    class IncompleteAuthConfig(AuthEnabledConfig):
        SUPABASE_JWKS_URL = ''

    with pytest.raises(RuntimeError, match='SUPABASE_JWKS_URL'):
        create_app(IncompleteAuthConfig, start_worker=False)

    class MissingPublishableKeyConfig(AuthEnabledConfig):
        SUPABASE_PUBLISHABLE_KEY = ''

    with pytest.raises(RuntimeError, match='SUPABASE_PUBLISHABLE_KEY'):
        create_app(MissingPublishableKeyConfig, start_worker=False)


def test_enabled_auth_accepts_complete_non_secret_contract():
    config = {name: getattr(AuthEnabledConfig, name) for name in dir(AuthEnabledConfig) if name.isupper()}
    validate_supabase_auth_config(config)
    assert config['SUPABASE_JWT_AUDIENCE'] == 'authenticated'
    assert config['BOOTSTRAP_PLATFORM_ADMIN_EMAIL'] == 'admin@example.com'


@pytest.mark.parametrize('name,value', [
    ('SUPABASE_URL', 'http://example.supabase.co'),
    ('SUPABASE_URL', 'https://example.supabase.co/not-an-origin'),
    ('SUPABASE_URL', 'https://example.supabase.co@evil.example'),
    ('SUPABASE_JWT_ISSUER', 'https://evil.example/auth/v1'),
    ('SUPABASE_JWT_ISSUER', 'https://example.supabase.co/auth/v2'),
    ('SUPABASE_JWKS_URL', 'https://evil.example/auth/v1/.well-known/jwks.json'),
    ('SUPABASE_JWKS_URL', 'https://example.supabase.co/auth/v1/not-jwks.json'),
])
def test_enabled_auth_rejects_untrusted_or_mismatched_endpoints(name, value):
    config = {key: getattr(AuthEnabledConfig, key) for key in dir(AuthEnabledConfig) if key.isupper()}
    config[name] = value
    with pytest.raises(RuntimeError):
        validate_supabase_auth_config(config)


@pytest.mark.parametrize('value', ['', 'not-an-email', 'admin @example.com'])
def test_enabled_auth_requires_a_normalizable_bootstrap_admin_email(value):
    config = {key: getattr(AuthEnabledConfig, key) for key in dir(AuthEnabledConfig) if key.isupper()}
    config['BOOTSTRAP_PLATFORM_ADMIN_EMAIL'] = value
    with pytest.raises(RuntimeError, match='BOOTSTRAP_PLATFORM_ADMIN_EMAIL'):
        validate_supabase_auth_config(config)
