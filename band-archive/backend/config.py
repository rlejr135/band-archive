import os


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
        'SUPABASE_JWT_AUDIENCE', 'SUPABASE_JWKS_URL',
    )
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise RuntimeError(
            'Supabase authentication is enabled but required configuration is missing: '
            + ', '.join(missing)
        )


class Config:
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024  # 200MB

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
