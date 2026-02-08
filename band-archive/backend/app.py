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
from config import DevelopmentConfig

load_dotenv()


def create_app(config_class=None):
    if config_class is None:
        config_name = os.getenv('FLASK_CONFIG', 'config.DevelopmentConfig')
        module_name, class_name = config_name.rsplit('.', 1)
        import importlib
        module = importlib.import_module(module_name)
        config_class = getattr(module, class_name)
    app = Flask(__name__)
    app.config.from_object(config_class)
    # Allow all origins, methods, and headers for development/testing
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

    db.init_app(app)
    Migrate(app, db)
    register_error_handlers(app)
    app.register_blueprint(songs_bp)
    app.register_blueprint(practice_logs_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(suggestions_bp)

    with app.app_context():
        db.create_all()
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
