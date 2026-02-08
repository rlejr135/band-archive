import os
import sys

import pytest

# Add backend directory to path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from extensions import db as _db
from app import create_app
from config import TestingConfig


@pytest.fixture
def app(tmp_path):
    test_app = create_app(TestingConfig)
    test_app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    os.makedirs(test_app.config['UPLOAD_FOLDER'], exist_ok=True)

    with test_app.app_context():
        _db.create_all()
        yield test_app
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
