import os


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


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///band_archive.db')


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:////data/band_archive.db')
