import os

from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate
from dotenv import load_dotenv

from extensions import db
from errors import register_error_handlers
from storage import storage
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
from config import DevelopmentConfig

load_dotenv()


def _run_migrations(app):
    """기존 테이블에 누락된 컬럼을 추가하는 스타트업 마이그레이션."""
    import sqlite3
    db_uri = app.config['SQLALCHEMY_DATABASE_URI']
    if not db_uri.startswith('sqlite'):
        return
    db_path = db_uri.replace('sqlite:///', '')
    try:
        conn = sqlite3.connect(db_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Vote counters are cache fields.  They are rebuilt from SongVote on
        # every SQLite startup migration so legacy/null values cannot drift.
        if 'song' in tables:
            song_columns = {row[1] for row in conn.execute('PRAGMA table_info(song)').fetchall()}
            for column in ('upvote_count', 'downvote_count', 'vote_score'):
                if column not in song_columns:
                    conn.execute(f'ALTER TABLE song ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')
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
            conn.execute('ALTER TABLE media ADD COLUMN original_filename VARCHAR(200)')
            app.logger.info('Migration: added original_filename column to media table')

        # Check personal_log table
        columns = [row[1] for row in conn.execute('PRAGMA table_info(personal_log)').fetchall()]
        if 'original_filename' not in columns:
            conn.execute('ALTER TABLE personal_log ADD COLUMN original_filename VARCHAR(200)')
            app.logger.info('Migration: added original_filename column to personal_log table')
        
        if 'file_size' not in columns:
            conn.execute('ALTER TABLE personal_log ADD COLUMN file_size INTEGER')
            app.logger.info('Migration: added file_size column to personal_log table')
        personal_log_columns = [row[1] for row in conn.execute('PRAGMA table_info(personal_log)').fetchall()]
        personal_log_additions = {
            'transcoding_status': "VARCHAR(20) DEFAULT 'not_required'",
            'audio_filename': 'VARCHAR(200)',
            'processing_error': 'VARCHAR(500)',
            'processing_started_at': 'DATETIME',
            'processing_completed_at': 'DATETIME',
            'processing_attempts': 'INTEGER DEFAULT 0',
            'processing_heartbeat_at': 'DATETIME',
        }
        for column, definition in personal_log_additions.items():
            if column not in personal_log_columns:
                conn.execute(f'ALTER TABLE personal_log ADD COLUMN {column} {definition}')
                app.logger.info('Migration: added %s column to personal_log table', column)
            
        # Check rehearsal table
        columns = [row[1] for row in conn.execute('PRAGMA table_info(rehearsal)').fetchall()]
        if 'location' not in columns:
            conn.execute('ALTER TABLE rehearsal ADD COLUMN location VARCHAR(200)')
            app.logger.info('Migration: added location column to rehearsal table')
        if 'latitude' not in columns:
            conn.execute('ALTER TABLE rehearsal ADD COLUMN latitude FLOAT')
            app.logger.info('Migration: added latitude column to rehearsal table')
        if 'longitude' not in columns:
            conn.execute('ALTER TABLE rehearsal ADD COLUMN longitude FLOAT')
            app.logger.info('Migration: added longitude column to rehearsal table')

        # Check media table for rehearsal_id and is_featured
        columns = [row[1] for row in conn.execute('PRAGMA table_info(media)').fetchall()]
        if 'rehearsal_id' not in columns:
            conn.execute('ALTER TABLE media ADD COLUMN rehearsal_id INTEGER REFERENCES rehearsal(id)')
            app.logger.info('Migration: added rehearsal_id column to media table')
        if 'is_featured' not in columns:
            conn.execute('ALTER TABLE media ADD COLUMN is_featured BOOLEAN DEFAULT 0')
            app.logger.info('Migration: added is_featured column to media table')
        if 'transcoding_status' not in columns:
            conn.execute("ALTER TABLE media ADD COLUMN transcoding_status VARCHAR(20) DEFAULT 'not_required'")
            app.logger.info('Migration: added transcoding_status column to media table')
        if 'audio_filename' not in columns:
            conn.execute('ALTER TABLE media ADD COLUMN audio_filename VARCHAR(200)')
            app.logger.info('Migration: added audio_filename column to media table')
        if 'processing_error' not in columns:
            conn.execute('ALTER TABLE media ADD COLUMN processing_error VARCHAR(500)')
            app.logger.info('Migration: added processing_error column to media table')
        if 'processing_started_at' not in columns:
            conn.execute('ALTER TABLE media ADD COLUMN processing_started_at DATETIME')
            app.logger.info('Migration: added processing_started_at column to media table')
        if 'processing_completed_at' not in columns:
            conn.execute('ALTER TABLE media ADD COLUMN processing_completed_at DATETIME')
            app.logger.info('Migration: added processing_completed_at column to media table')
        if 'processing_attempts' not in columns:
            conn.execute('ALTER TABLE media ADD COLUMN processing_attempts INTEGER DEFAULT 0')
            app.logger.info('Migration: added processing_attempts column to media table')
        if 'processing_heartbeat_at' not in columns:
            conn.execute('ALTER TABLE media ADD COLUMN processing_heartbeat_at DATETIME')
            app.logger.info('Migration: added processing_heartbeat_at column to media table')

        # Legacy rows predate the explicit lifecycle. Never leave non-video rows pending.
        conn.execute("UPDATE media SET transcoding_status = 'queued' "
                     "WHERE file_type = 'video' AND (transcoding_status IS NULL OR transcoding_status = 'pending')")
        conn.execute("UPDATE media SET transcoding_status = 'not_required' "
                     "WHERE (file_type IS NULL OR file_type != 'video') "
                     "AND (transcoding_status IS NULL OR transcoding_status = 'pending')")
        conn.execute('UPDATE media SET processing_attempts = 0 WHERE processing_attempts IS NULL')
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
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
                    app.logger.info('Migration: added %s column to %s table', column, table)

        # Drop removed tables
        conn.execute('DROP TABLE IF EXISTS practice_log')

        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f'Startup migration failed: {e}')


def create_app(config_class=None, start_worker=None):
    if config_class is None:
        config_name = os.getenv('FLASK_CONFIG', 'config.DevelopmentConfig')
        module_name, class_name = config_name.rsplit('.', 1)
        import importlib
        module = importlib.import_module(module_name)
        config_class = getattr(module, class_name)
    app = Flask(__name__)
    app.config.from_object(config_class)
    if app.debug:
        CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    else:
        allowed_origins = [
            origin.strip()
            for origin in os.getenv('CORS_ALLOWED_ORIGINS', '').split(',')
            if origin.strip()
        ]
        CORS(app, resources={r"/*": {"origins": allowed_origins}}, supports_credentials=True)

    db.init_app(app)
    Migrate(app, db)
    register_error_handlers(app)
    app.register_blueprint(songs_bp)
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
        db.create_all()
        _run_migrations(app)
        from routes.uploads import recover_multipart_upload_sessions
        recover_multipart_upload_sessions(app)

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
