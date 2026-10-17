import os
import re
from urllib.parse import urlsplit


def _env_flag(name, default='false'):
    return os.getenv(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_int(name, default, minimum=0):
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f'{name} must be at least {minimum}')
    return value


def validate_supabase_auth_config(config):
    """Fail fast only when this deployment opts into protected v2 auth."""
    if not config.get('SUPABASE_AUTH_ENABLED'):
        return
    required = (
        'SUPABASE_URL', 'SUPABASE_PROJECT_REF', 'SUPABASE_JWT_ISSUER',
        'SUPABASE_JWT_AUDIENCE', 'SUPABASE_JWKS_URL', 'SUPABASE_PUBLISHABLE_KEY',
    )
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise RuntimeError(
            'Supabase authentication is enabled but required configuration is missing: '
            + ', '.join(missing)
        )

    bootstrap_email = _normalize_bootstrap_email(config.get('BOOTSTRAP_PLATFORM_ADMIN_EMAIL'))
    if not bootstrap_email:
        raise RuntimeError(
            'Supabase authentication is enabled but required configuration is missing: '
            'BOOTSTRAP_PLATFORM_ADMIN_EMAIL'
        )
    config['BOOTSTRAP_PLATFORM_ADMIN_EMAIL'] = bootstrap_email

    project_ref = config['SUPABASE_PROJECT_REF'].strip().lower()
    if not re.fullmatch(r'[a-z0-9-]+', project_ref):
        raise RuntimeError('SUPABASE_PROJECT_REF must be a valid Supabase project reference.')
    config['SUPABASE_PROJECT_REF'] = project_ref

    supabase_url = _https_origin(config['SUPABASE_URL'], 'SUPABASE_URL')
    expected_host = f'{project_ref}.supabase.co'
    if supabase_url.hostname != expected_host:
        raise RuntimeError(
            'SUPABASE_URL must use the trusted origin for SUPABASE_PROJECT_REF '
            f'({expected_host}).'
        )
    canonical_origin = f'https://{expected_host}'
    if supabase_url.geturl() != canonical_origin:
        raise RuntimeError('SUPABASE_URL must be the HTTPS origin without a path, query, or fragment.')
    config['SUPABASE_URL'] = canonical_origin

    issuer = _https_url(config['SUPABASE_JWT_ISSUER'], 'SUPABASE_JWT_ISSUER')
    jwks = _https_url(config['SUPABASE_JWKS_URL'], 'SUPABASE_JWKS_URL')
    if _origin(issuer) != canonical_origin or issuer.path != '/auth/v1':
        raise RuntimeError('SUPABASE_JWT_ISSUER must be exactly SUPABASE_URL/auth/v1.')
    if _origin(jwks) != canonical_origin or jwks.path != '/auth/v1/.well-known/jwks.json':
        raise RuntimeError(
            'SUPABASE_JWKS_URL must be exactly '
            'SUPABASE_URL/auth/v1/.well-known/jwks.json.'
        )
    config['SUPABASE_JWT_ISSUER'] = f'{canonical_origin}/auth/v1'
    config['SUPABASE_JWKS_URL'] = f'{canonical_origin}/auth/v1/.well-known/jwks.json'


def _normalize_bootstrap_email(value):
    if not isinstance(value, str):
        return ''
    email = value.strip().casefold()
    if not email or email.count('@') != 1:
        return ''
    local, domain = email.split('@')
    if not local or not domain or any(char.isspace() for char in email):
        return ''
    return email


def _https_url(value, name):
    if not isinstance(value, str):
        raise RuntimeError(f'{name} must be an HTTPS URL.')
    parsed = urlsplit(value.strip())
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port is not None or parsed.query or parsed.fragment):
        raise RuntimeError(f'{name} must be an HTTPS URL without credentials, port, query, or fragment.')
    return parsed


def _https_origin(value, name):
    parsed = _https_url(value, name)
    if parsed.path not in ('', '/'):
        raise RuntimeError(f'{name} must be an HTTPS origin without a path.')
    return parsed


def _origin(parsed):
    return f'{parsed.scheme}://{parsed.hostname}'


class Config:
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024  # 200MB
    # Place-search controls are intentionally process-local. See the operations
    # note: per-worker caps multiply across workers and reset on restart.
    PLACE_SEARCH_REQUEST_LIMIT = 10
    PLACE_SEARCH_UPSTREAM_LIMIT = 60
    PLACE_SEARCH_WINDOW_SECONDS = 60
    PLACE_SEARCH_CACHE_TTL_SECONDS = 30
    PLACE_SEARCH_CACHE_MAX_ENTRIES = 512
    PLACE_SEARCH_MAX_SUBJECTS = 4096
    PLACE_SEARCH_MAX_CONCURRENT_UPSTREAM = 2
    # Emergency retirement switch for the unauthenticated compatibility API.
    # Reads remain available so legacy clients can be migrated gracefully.
    LEGACY_V1_WRITES_ENABLED = _env_flag('LEGACY_V1_WRITES_ENABLED', 'true')

    # S3 호환 스토리지 설정 (R2, B2 등)
    S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL')
    S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY')
    S3_SECRET_KEY = os.getenv('S3_SECRET_KEY')
    S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
    # Deliberately separate from R2 credentials: this only authorizes the
    # narrow internal migration inventory response.
    R2_MIGRATION_TOKEN = os.getenv('R2_MIGRATION_TOKEN')
    S3_PRESIGN_EXPIRES = 3600  # presigned URL 유효시간 (초)
    # Large source videos can legitimately exceed five minutes. Workers send a
    # heartbeat while FFmpeg runs; stale jobs are recovered on next startup.
    AUDIO_PROCESSING_TIMEOUT_SECONDS = int(os.getenv('AUDIO_PROCESSING_TIMEOUT_SECONDS', '1800'))
    AUDIO_PROCESSING_HEARTBEAT_SECONDS = int(os.getenv('AUDIO_PROCESSING_HEARTBEAT_SECONDS', '15'))
    AUDIO_PROCESSING_STALE_SECONDS = int(os.getenv('AUDIO_PROCESSING_STALE_SECONDS', '3600'))
    AUDIO_WORKER_POLL_SECONDS = float(os.getenv('AUDIO_WORKER_POLL_SECONDS', '5'))
    # PostgreSQL cutover must not silently resume imported jobs or provider
    # upload sessions. These independent opt-ins do not affect SQLite.
    PG_AUDIO_WORKER_ENABLED = _env_flag('PG_AUDIO_WORKER_ENABLED')
    PG_MULTIPART_RECOVERY_ENABLED = _env_flag('PG_MULTIPART_RECOVERY_ENABLED')
    # File deletion has a separate bounded worker. PostgreSQL startup never
    # enables it implicitly; deploy config must opt in after schema review.
    PG_R2_DELETION_WORKER_ENABLED = _env_flag('PG_R2_DELETION_WORKER_ENABLED')
    R2_DELETION_WORKER_POLL_SECONDS = _env_int('R2_DELETION_WORKER_POLL_SECONDS', 5, minimum=1)
    R2_DELETION_WORKER_BATCH_SIZE = _env_int('R2_DELETION_WORKER_BATCH_SIZE', 2, minimum=1)
    R2_DELETION_WORKER_LEASE_SECONDS = _env_int('R2_DELETION_WORKER_LEASE_SECONDS', 120, minimum=30)
    R2_DELETION_MAX_ATTEMPTS = _env_int('R2_DELETION_MAX_ATTEMPTS', 8, minimum=1)

    # Supabase Auth is intentionally optional during the v1 compatibility
    # period.  Production enables it only after every required setting is set;
    # public v1 routes continue to boot while it is disabled.
    SUPABASE_AUTH_ENABLED = _env_flag('SUPABASE_AUTH_ENABLED')
    SUPABASE_URL = os.getenv('SUPABASE_URL', '').rstrip('/')
    SUPABASE_PROJECT_REF = os.getenv('SUPABASE_PROJECT_REF', '')
    SUPABASE_JWT_ISSUER = os.getenv(
        'SUPABASE_JWT_ISSUER',
        f'{SUPABASE_URL}/auth/v1' if SUPABASE_URL else '',
    ).rstrip('/')
    SUPABASE_JWT_AUDIENCE = os.getenv('SUPABASE_JWT_AUDIENCE', 'authenticated')
    SUPABASE_JWKS_URL = os.getenv(
        'SUPABASE_JWKS_URL',
        f'{SUPABASE_URL}/auth/v1/.well-known/jwks.json' if SUPABASE_URL else '',
    )
    # The public key identifies this application to the Auth user endpoint;
    # user identity remains the separately supplied bearer access token.
    SUPABASE_PUBLISHABLE_KEY = os.getenv('SUPABASE_PUBLISHABLE_KEY', '')
    SUPABASE_JWKS_CACHE_SECONDS = _env_int('SUPABASE_JWKS_CACHE_SECONDS', 300, minimum=1)
    SUPABASE_HTTP_TIMEOUT_SECONDS = _env_int('SUPABASE_HTTP_TIMEOUT_SECONDS', 5, minimum=1)
    SUPABASE_JWT_LEEWAY_SECONDS = _env_int('SUPABASE_JWT_LEEWAY_SECONDS', 30, minimum=0)
    BOOTSTRAP_PLATFORM_ADMIN_EMAIL = os.getenv('BOOTSTRAP_PLATFORM_ADMIN_EMAIL', '')


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///band_archive.db')


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:////data/band_archive.db')
