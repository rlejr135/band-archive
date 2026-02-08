import os
import sys
import tempfile
import pytest

# Add backend directory to path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from extensions import db as _db
from app import app as _app


@pytest.fixture
def app(tmp_path):
    _app.config['TESTING'] = True
    _app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    _app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    _app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
    os.makedirs(_app.config['UPLOAD_FOLDER'], exist_ok=True)

    with _app.app_context():
        _db.create_all()
        yield _app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sample_song(client):
    """Create a sample song and return its data."""
    resp = client.post('/songs', json={
        'title': 'Bohemian Rhapsody',
        'artist': 'Queen',
        'genre': 'Rock',
        'difficulty': 4,
        'status': 'Practice',
    })
    return resp.get_json()
