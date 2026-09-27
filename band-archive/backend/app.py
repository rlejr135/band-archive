import os
import re
import logging
import time

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_migrate import Migrate
from dotenv import load_dotenv

from extensions import db
from errors import register_error_handlers
from storage import storage
from migration_bootstrap import (
    MIGRATIONS_DIRECTORY, PHASE4_REVISION, baseline_metadata, migration_head_revision, phase4_metadata,
    schema_contract_differences,
)
from routes.songs import songs_bp
from routes.dashboard import dashboard_bp
from routes.suggestions import suggestions_bp
from routes.members import members_bp
from routes.personal_logs import personal_logs_bp as member_personal_logs_bp
from routes.announcements import announcements_bp
from routes.rehearsals import rehearsals_bp
from routes.search import search_bp
from routes.comments import comments_bp
from routes.gallery import gallery_bp
from routes.uploads import uploads_bp
from routes.migrations import migrations_bp
from routes.auth import auth_bp
from routes.bands import bands_bp
from routes.content_reads import content_reads_bp
from config import DevelopmentConfig, validate_supabase_auth_config

load_dotenv()


_SQLITE_MIGRATION_LOCK_RETRIES = 8
_SQLITE_MIGRATION_BUSY_TIMEOUT_MS = 750
_BAND_SCHEMA_TABLES = {
    'band', 'band_membership', 'band_creation_request', 'band_invite_code', 'audit_event',
}
_LEGACY_CLIENT_VERSION_RE = re.compile(
    r'^[vV]?[0-9]{1,5}(?:\.[0-9]{1,5}){0,3}$'
)
_LEGACY_CLIENT_VERSION_MAX_LENGTH = 25


def _sanitize_legacy_client_version(raw_version):
    """Return only a deliberately tiny, non-identifying release version.

    Historical clients sometimes put arbitrary build metadata in this header.
    It is telemetry, not an identity field: accept one to four ASCII numeric
    components (with an optional ``v``) and omit everything else.  In
    particular, prerelease/build suffixes are not safe to retain because they
    can contain capability tokens or other caller-controlled data.
    """
    if not isinstance(raw_version, str) or len(raw_version) > _LEGACY_CLIENT_VERSION_MAX_LENGTH:
        return ''
    return raw_version if _LEGACY_CLIENT_VERSION_RE.fullmatch(raw_version) else ''


def _band_schema_ready():
    """Return true only after the isolated tenant backfill is validated."""
    from sqlalchemy import inspect, text

    with db.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        if not _BAND_SCHEMA_TABLES.issubset(tables) or 'alembic_version' not in tables:
            return False
        versions = connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
        if versions != [migration_head_revision()]:
            return False
        if schema_contract_differences(connection):
            return False
        # Expansion is intentionally safe before this marker exists.  Keep
        # v2 tenant routes gated then; v1 stays compatible with nullable cols.
        if 'tenant_backfill_state' not in tables:
            return False
        state = connection.execute(text(
            "SELECT status FROM tenant_backfill_state WHERE key = 'phase4_tenant_scope'"
        )).scalar_one_or_none()
        return state == 'validated'


def _phase4_orm_schema_ready(app):
    """Prove ORM-mapped Phase 4 columns exist before handlers query them."""
    from sqlalchemy import inspect, text

    with db.engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        # Test fixtures explicitly create current metadata after the factory;
        # they are not compatibility databases and carry no Alembic history.
        if app.testing and 'alembic_version' not in tables:
            return 'song' in tables and 'band_id' in {item['name'] for item in inspector.get_columns('song')}
        if 'alembic_version' not in tables or 'tenant_backfill_state' not in tables:
            return False
        versions = connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
        # v05 deliberately leaves scope columns nullable while its offline
        # backfill is pending.  Legacy GETs remain available at that exact
        # schema, while writes and all v2 tenant endpoints stay gated until
        # the Phase 6 head contract has been applied.
        if versions == [PHASE4_REVISION]:
            return not schema_contract_differences(connection, metadata=phase4_metadata())
        return versions == [migration_head_revision()] and not schema_contract_differences(connection)


def _add_sqlite_column(conn, table, column, definition):
    """Add a known, additive column and tolerate a competing startup winning."""
    import sqlite3

    try:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        return True
    except sqlite3.OperationalError as error:
        # A second process can have completed the same ALTER after its caller's
        # PRAGMA check.  The desired postcondition is already satisfied.
        if 'duplicate column name' in str(error).lower():
            return False
        raise


def _run_migrations(app, _lock_attempt=0):
    """기존 테이블에 누락된 컬럼을 추가하는 스타트업 마이그레이션."""
    import sqlite3
    db_uri = app.config['SQLALCHEMY_DATABASE_URI']
    if not db_uri.startswith('sqlite'):
        return
    db_path = db_uri.replace('sqlite:///', '')
    conn = None
    try:
        # Schema changes are transactional in SQLite.  Taking the write lock
        # before inspecting columns prevents two app processes from both
        # observing a missing column and then racing to ALTER it.
        conn = sqlite3.connect(db_path, timeout=_SQLITE_MIGRATION_BUSY_TIMEOUT_MS / 1000)
        conn.execute(f'PRAGMA busy_timeout = {_SQLITE_MIGRATION_BUSY_TIMEOUT_MS}')
        conn.execute('BEGIN IMMEDIATE')
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Comments existed before playback-position links.  Keep this
        # independent of the media migrations because a partially restored
        # database can have comments even when another domain table is absent.
        if 'comment' in tables:
            comment_columns = {row[1] for row in conn.execute('PRAGMA table_info(comment)').fetchall()}
            if 'timestamp_seconds' not in comment_columns:
                if _add_sqlite_column(conn, 'comment', 'timestamp_seconds', 'FLOAT'):
                    app.logger.info('Migration: added timestamp_seconds column to comment table')
        # Vote counters are cache fields.  They are rebuilt from SongVote on
        # every SQLite startup migration so legacy/null values cannot drift.
        if 'song' in tables:
            song_columns = {row[1] for row in conn.execute('PRAGMA table_info(song)').fetchall()}
            for column in ('upvote_count', 'downvote_count', 'vote_score'):
                if column not in song_columns:
                    if _add_sqlite_column(conn, 'song', column, 'INTEGER NOT NULL DEFAULT 0'):
                        app.logger.info('Migration: added %s column to song table', column)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS song_vote (
                    id INTEGER PRIMARY KEY,
                    song_id INTEGER NOT NULL REFERENCES song(id) ON DELETE CASCADE,
                    voter_hash VARCHAR(64) NOT NULL,
                    value INTEGER NOT NULL CHECK (value IN (-1, 1)),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_song_vote_voter UNIQUE (song_id, voter_hash)
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS ix_song_vote_song_id ON song_vote(song_id)')
            conn.execute("UPDATE song SET upvote_count = (SELECT COUNT(*) FROM song_vote "
                         "WHERE song_vote.song_id = song.id AND song_vote.value = 1)")
            conn.execute("UPDATE song SET downvote_count = (SELECT COUNT(*) FROM song_vote "
                         "WHERE song_vote.song_id = song.id AND song_vote.value = -1)")
            conn.execute('UPDATE song SET vote_score = upvote_count - downvote_count')

        # A partially restored legacy database can contain Song but not the
        # media domain.  Vote schema must still migrate; leave the unrelated
        # media/personal-log migrations untouched until those tables exist.
        if 'media' not in tables:
            conn.commit()
            conn.close()
            return

        # Check media table
        columns = [row[1] for row in conn.execute('PRAGMA table_info(media)').fetchall()]
        if 'original_filename' not in columns:
            if _add_sqlite_column(conn, 'media', 'original_filename', 'VARCHAR(200)'):
                app.logger.info('Migration: added original_filename column to media table')

        # Each legacy table can be absent independently; do not let that stop
        # media's 720p schema migration on a partially restored database.
        if 'personal_log' in tables:
            columns = [row[1] for row in conn.execute('PRAGMA table_info(personal_log)').fetchall()]
            if 'original_filename' not in columns:
                if _add_sqlite_column(conn, 'personal_log', 'original_filename', 'VARCHAR(200)'):
                    app.logger.info('Migration: added original_filename column to personal_log table')
            if 'file_size' not in columns:
                if _add_sqlite_column(conn, 'personal_log', 'file_size', 'INTEGER'):
                    app.logger.info('Migration: added file_size column to personal_log table')
            personal_log_additions = {
                'transcoding_status': "VARCHAR(20) DEFAULT 'not_required'", 'audio_filename': 'VARCHAR(200)',
                'processing_error': 'VARCHAR(500)', 'processing_started_at': 'DATETIME',
                'processing_completed_at': 'DATETIME', 'processing_attempts': 'INTEGER DEFAULT 0',
                'processing_heartbeat_at': 'DATETIME',
            }
            for column, definition in personal_log_additions.items():
                if column not in columns:
                    if _add_sqlite_column(conn, 'personal_log', column, definition):
                        app.logger.info('Migration: added %s column to personal_log table', column)
            
        if 'rehearsal' in tables:
            columns = [row[1] for row in conn.execute('PRAGMA table_info(rehearsal)').fetchall()]
            if 'location' not in columns:
                if _add_sqlite_column(conn, 'rehearsal', 'location', 'VARCHAR(200)'):
                    app.logger.info('Migration: added location column to rehearsal table')
            if 'latitude' not in columns:
                if _add_sqlite_column(conn, 'rehearsal', 'latitude', 'FLOAT'):
                    app.logger.info('Migration: added latitude column to rehearsal table')
            if 'longitude' not in columns:
                if _add_sqlite_column(conn, 'rehearsal', 'longitude', 'FLOAT'):
                    app.logger.info('Migration: added longitude column to rehearsal table')

        # Check media table for rehearsal_id and is_featured
        columns = [row[1] for row in conn.execute('PRAGMA table_info(media)').fetchall()]
        if 'rehearsal_id' not in columns:
            if _add_sqlite_column(conn, 'media', 'rehearsal_id', 'INTEGER REFERENCES rehearsal(id)'):
                app.logger.info('Migration: added rehearsal_id column to media table')
        if 'is_featured' not in columns:
            if _add_sqlite_column(conn, 'media', 'is_featured', 'BOOLEAN DEFAULT 0'):
                app.logger.info('Migration: added is_featured column to media table')
        if 'transcoding_status' not in columns:
            if _add_sqlite_column(conn, 'media', 'transcoding_status', "VARCHAR(20) DEFAULT 'not_required'"):
                app.logger.info('Migration: added transcoding_status column to media table')
        if 'audio_filename' not in columns:
            if _add_sqlite_column(conn, 'media', 'audio_filename', 'VARCHAR(200)'):
                app.logger.info('Migration: added audio_filename column to media table')
        if 'processing_error' not in columns:
            if _add_sqlite_column(conn, 'media', 'processing_error', 'VARCHAR(500)'):
                app.logger.info('Migration: added processing_error column to media table')
        if 'processing_started_at' not in columns:
            if _add_sqlite_column(conn, 'media', 'processing_started_at', 'DATETIME'):
                app.logger.info('Migration: added processing_started_at column to media table')
        if 'processing_completed_at' not in columns:
            if _add_sqlite_column(conn, 'media', 'processing_completed_at', 'DATETIME'):
                app.logger.info('Migration: added processing_completed_at column to media table')
        if 'processing_attempts' not in columns:
            if _add_sqlite_column(conn, 'media', 'processing_attempts', 'INTEGER DEFAULT 0'):
                app.logger.info('Migration: added processing_attempts column to media table')
        if 'processing_heartbeat_at' not in columns:
            if _add_sqlite_column(conn, 'media', 'processing_heartbeat_at', 'DATETIME'):
                app.logger.info('Migration: added processing_heartbeat_at column to media table')
        video_720_additions = {
            'video_720_filename': 'VARCHAR(300)',
            'video_720_source_etag': 'VARCHAR(200)',
            'video_720_profile': 'VARCHAR(100)',
            'video_720_completed_at': 'DATETIME',
            'original_pruned_at': 'DATETIME',
        }
        columns = {row[1] for row in conn.execute('PRAGMA table_info(media)').fetchall()}
        for column, definition in video_720_additions.items():
            if column not in columns:
                if _add_sqlite_column(conn, 'media', column, definition):
                    app.logger.info('Migration: added %s column to media table', column)

        # SongVote is retained as legacy data because it cannot be mapped to a
        # specific Media without guessing.  New voting state is independent
        # and is rebuilt from MediaVote rows to keep the cached counters safe.
        media_columns = {row[1] for row in conn.execute('PRAGMA table_info(media)').fetchall()}
        for column in ('upvote_count', 'downvote_count', 'vote_score'):
            if column not in media_columns:
                if _add_sqlite_column(conn, 'media', column, 'INTEGER NOT NULL DEFAULT 0'):
                    app.logger.info('Migration: added %s column to media table', column)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS media_vote (
                id INTEGER PRIMARY KEY,
                media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                voter_hash VARCHAR(64) NOT NULL,
                value INTEGER NOT NULL CHECK (value IN (-1, 1)),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_media_vote_voter UNIQUE (media_id, voter_hash)
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_media_vote_media_id ON media_vote(media_id)')
        conn.execute("UPDATE media SET upvote_count = (SELECT COUNT(*) FROM media_vote "
                     "WHERE media_vote.media_id = media.id AND media_vote.value = 1)")
        conn.execute("UPDATE media SET downvote_count = (SELECT COUNT(*) FROM media_vote "
                     "WHERE media_vote.media_id = media.id AND media_vote.value = -1)")
        conn.execute('UPDATE media SET vote_score = upvote_count - downvote_count')

        # Legacy rows predate the explicit lifecycle. Never leave non-video rows pending.
        conn.execute("UPDATE media SET transcoding_status = 'queued' "
                     "WHERE file_type = 'video' AND (transcoding_status IS NULL OR transcoding_status = 'pending')")
        conn.execute("UPDATE media SET transcoding_status = 'not_required' "
                     "WHERE (file_type IS NULL OR file_type != 'video') "
                     "AND (transcoding_status IS NULL OR transcoding_status = 'pending')")
        conn.execute('UPDATE media SET processing_attempts = 0 WHERE processing_attempts IS NULL')
        if 'personal_log' in tables:
            conn.execute("UPDATE personal_log SET transcoding_status = 'queued' "
                         "WHERE file_type = 'video' AND "
                         "(transcoding_status IS NULL OR transcoding_status IN ('pending', 'not_required'))")
            conn.execute("UPDATE personal_log SET transcoding_status = 'not_required' "
                         "WHERE (file_type IS NULL OR file_type != 'video') "
                         "AND (transcoding_status IS NULL OR transcoding_status = 'pending')")
            conn.execute('UPDATE personal_log SET processing_attempts = 0 WHERE processing_attempts IS NULL')

        # Resumable multipart uploads persist only server-owned identifiers and
        # a hash of the one-time capability returned by initiate.  SQLite ALTER
        # additions remain nullable so existing sessions can be safely expired
        # instead of becoming accessible by session ID alone.
        multipart_additions = {
            'multipart_upload_session': {
                'capability_token_hash': 'VARCHAR(255)',
                'completion_started_at': 'DATETIME',
            },
            'personal_log_multipart_upload_session': {
                'capability_token_hash': 'VARCHAR(255)',
                'completion_started_at': 'DATETIME',
            },
            'multipart_upload_part': {
                'etag': 'VARCHAR(500)',
                'uploaded_bytes': 'BIGINT',
                'checksum': 'VARCHAR(200)',
                'acknowledged_at': 'DATETIME',
            },
            'personal_log_multipart_upload_part': {
                'etag': 'VARCHAR(500)',
                'uploaded_bytes': 'BIGINT',
                'checksum': 'VARCHAR(200)',
                'acknowledged_at': 'DATETIME',
            },
        }
        for table, additions in multipart_additions.items():
            if table not in tables:
                continue
            columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()}
            for column, definition in additions.items():
                if column not in columns:
                    if _add_sqlite_column(conn, table, column, definition):
                        app.logger.info('Migration: added %s column to %s table', column, table)

        # Drop removed tables
        conn.execute('DROP TABLE IF EXISTS practice_log')

        conn.commit()
        conn.close()
    except sqlite3.OperationalError as error:
        if conn is not None:
            conn.rollback()
            conn.close()
        if 'locked' in str(error).lower() and _lock_attempt < _SQLITE_MIGRATION_LOCK_RETRIES:
            # Keep startup bounded while allowing an overlapping process to
            # finish its migration transaction.  Each retry rechecks schema.
            time.sleep(0.05 * (_lock_attempt + 1))
            return _run_migrations(app, _lock_attempt + 1)
        app.logger.exception('Startup migration failed')
        raise RuntimeError('SQLite startup migration failed') from error
    except Exception as error:
        if conn is not None:
            conn.rollback()
            conn.close()
        app.logger.exception('Startup migration failed')
        raise RuntimeError('SQLite startup migration failed') from error


def create_app(config_class=None, start_worker=None):
    if config_class is None:
        config_name = os.getenv('FLASK_CONFIG', 'config.DevelopmentConfig')
        module_name, class_name = config_name.rsplit('.', 1)
        import importlib
        module = importlib.import_module(module_name)
        config_class = getattr(module, class_name)
    app = Flask(__name__)
    app.config.from_object(config_class)
    # Dedicated rollout telemetry logger: configuring its level/propagation is
    # idempotent and it owns no handler, so Flask/root logging emits once.
    legacy_usage_logger = logging.getLogger('band_archive.legacy_v1')
    legacy_usage_logger.setLevel(logging.INFO)
    legacy_usage_logger.propagate = True
    # Other test/process logging setup may have disabled a named logger.  This
    # telemetry logger is explicitly owned by the app and must remain usable.
    legacy_usage_logger.disabled = False
    app.extensions['legacy_v1_usage_logger'] = legacy_usage_logger
    validate_supabase_auth_config(app.config)
    if app.debug:
        CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    else:
        allowed_origins = [
            origin.strip()
            for origin in os.getenv('CORS_ALLOWED_ORIGINS', '').split(',')
            if origin.strip()
        ]
        CORS(app, resources={r"/*": {"origins": allowed_origins}}, supports_credentials=True)

    @app.before_request
    def _require_phase4_orm_schema():
        # The retirement switch is intentionally the very first v1 mutation
        # decision.  It is independent of migration readiness and must not
        # parse a body or reach a handler before returning its stable status.
        from legacy_scope import legacy_v1_request, legacy_writes_enabled
        if (request.method not in {'GET', 'HEAD', 'OPTIONS'}
                and legacy_v1_request() and not legacy_writes_enabled()):
            return jsonify(
                error='legacy_v1_writes_disabled',
                message='Legacy v1 writes have been retired.',
            ), 410
        if request.method == 'OPTIONS' or request.path == '/health' or request.path.startswith('/api/v2/'):
            return None
        if app.config.get('PHASE4_ORM_SCHEMA_READY', False):
            if (request.method not in {'GET', 'HEAD', 'OPTIONS'}
                    and not app.config.get('BAND_API_SCHEMA_READY', False)):
                return jsonify(error='tenant_backfill_required',
                               message='Phase 4 tenant backfill is required before writes.'), 503
            return None
        # Current-schema unit fixtures are initialized after create_app().
        if app.testing and _phase4_orm_schema_ready(app):
            return None
        return jsonify(error='tenant_schema_unavailable',
                       message='Phase 4 database migration is required.'), 503

    @app.after_request
    def _log_legacy_v1_usage(response):
        """One privacy-safe usage event for every historical API request."""
        try:
            from legacy_scope import legacy_v1_request

            if legacy_v1_request():
                # Never log arbitrary headers, query strings, request bodies,
                # filenames, identities, or capability tokens.  Client
                # version is useful rollout telemetry but bounded/sanitized.
                client_version = _sanitize_legacy_client_version(
                    request.headers.get('X-Client-Version', '')
                )
                rule = request.url_rule.rule if request.url_rule is not None else '<unmatched>'
                # ``Logger.info`` honors process-global ``logging.disable``.
                # Compatibility telemetry owns this named logger, so dispatch
                # the INFO record directly while still propagating through the
                # normal handler chain exactly once.
                legacy_usage_logger._log(  # pylint: disable=protected-access
                    logging.INFO,
                    'legacy_v1_usage route=%s method=%s status=%s client_version=%s',
                    (rule, request.method, response.status_code, client_version or '-'),
                )
        except Exception:
            # Observability must never alter a public request's outcome.
            pass
        return response

    db.init_app(app)
    # Keep Flask-Migrate pointed at the checked-in baseline.  The existing
    # create_all/startup migration path below remains the production path
    # until the later Postgres cutover; this only establishes the toolchain.
    Migrate(app, db, directory=str(MIGRATIONS_DIRECTORY))
    register_error_handlers(app)

    from legacy_scope import LegacyScopeUnavailable

    @app.errorhandler(LegacyScopeUnavailable)
    def _legacy_scope_unavailable(error):
        # Do not let an ambiguous/missing backfill marker degrade into an
        # unscoped v1 query.  Leave every other exception to Flask's normal
        # error handling.
        return jsonify(
            error='tenant_scope_unavailable',
            message='Validated legacy tenant scope is unavailable.',
        ), 503
    app.register_blueprint(songs_bp)
    app.register_blueprint(migrations_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(bands_bp)
    app.register_blueprint(content_reads_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(suggestions_bp)
    app.register_blueprint(members_bp)
    app.register_blueprint(member_personal_logs_bp)
    app.register_blueprint(announcements_bp)
    app.register_blueprint(rehearsals_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(comments_bp)
    app.register_blueprint(gallery_bp)
    app.register_blueprint(uploads_bp)

    storage.init_app(app)

    with app.app_context():
        # The v1 compatibility path may create only the historical schema.
        # Alembic-managed auth and later multiband tables must be created by
        # migration_runner before a process enables the protected API.
        baseline_metadata().create_all(db.engine)
        _run_migrations(app)
        # v2 routes remain registered for a deterministic response, but are
        # disabled until the isolated migration runner has produced head.
        app.config['BAND_API_SCHEMA_READY'] = _band_schema_ready()
        app.config['PHASE4_ORM_SCHEMA_READY'] = _phase4_orm_schema_ready(app)
        from routes.uploads import recover_multipart_upload_sessions
        recover_multipart_upload_sessions(app)
        if app.config.get('SUPABASE_AUTH_ENABLED'):
            from auth_service import assert_auth_schema_ready
            assert_auth_schema_ready()

    if start_worker is None:
        start_worker = not app.testing
    if start_worker:
        from media_processing import start_worker as start_audio_worker
        start_audio_worker(app)

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
