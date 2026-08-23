import os
import sys

import pytest

# Add backend directory to path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from extensions import db as _db
from app import create_app
from config import TestingConfig
from storage import storage


@pytest.fixture(autouse=True)
def mock_object_storage(monkeypatch):
    """Keep every test local; routes must never contact R2."""
    objects = {}

    def upload(key, file_obj, content_type=None):
        objects[key] = file_obj.read()
        file_obj.seek(0)

    def download(key, file_obj):
        file_obj.write(objects.get(key, b'video-source'))

    def head(key):
        return {'ContentLength': len(objects.get(key, b''))}

    monkeypatch.setattr(storage, 'upload', upload)
    monkeypatch.setattr(storage, 'download', download)
    monkeypatch.setattr(storage, 'exists', lambda key: True)
    monkeypatch.setattr(storage, 'delete', lambda key: objects.pop(key, None))
    monkeypatch.setattr(storage, 'copy', lambda src, dst: objects.__setitem__(dst, objects.get(src, b'')))
    monkeypatch.setattr(storage, 'generate_url', lambda key, expires_in=None: f'https://storage.test/{key}')
    monkeypatch.setattr(storage, 'generate_upload_url',
                        lambda key, content_type=None, expires_in=600: f'https://storage.test/upload/{key}')
    monkeypatch.setattr(storage, 'head', head)
    monkeypatch.setattr(storage, 'create_multipart_upload', lambda key, content_type: 'mock-upload-id')
    monkeypatch.setattr(storage, 'generate_upload_part_url',
                        lambda key, upload_id, part_number: f'https://storage.test/{upload_id}/{part_number}')
    monkeypatch.setattr(storage, 'complete_multipart_upload', lambda key, upload_id, parts: {'Key': key})
    monkeypatch.setattr(storage, 'abort_multipart_upload', lambda key, upload_id: None)


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
