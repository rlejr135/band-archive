import os

from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate
from dotenv import load_dotenv

from extensions import db
from errors import register_error_handlers
from routes.songs import songs_bp
from routes.practice_logs import practice_logs_bp
from routes.dashboard import dashboard_bp
from routes.suggestions import suggestions_bp
from routes.members import members_bp
from routes.personal_logs import personal_logs_bp as member_personal_logs_bp
from routes.announcements import announcements_bp
from routes.rehearsals import rehearsals_bp
from routes.search import search_bp
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

        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f'Startup migration failed: {e}')


def create_app(config_class=None):
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
    app.register_blueprint(practice_logs_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(suggestions_bp)
    app.register_blueprint(members_bp)
    app.register_blueprint(member_personal_logs_bp)
    app.register_blueprint(announcements_bp)
    app.register_blueprint(rehearsals_bp)
    app.register_blueprint(search_bp)

    with app.app_context():
        db.create_all()
        _run_migrations(app)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
