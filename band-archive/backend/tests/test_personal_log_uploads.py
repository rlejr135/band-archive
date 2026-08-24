import sqlite3

from flask import Flask
import pytest

from app import _run_migrations
from extensions import db
from media_processing import claim_next_queued_item, create_media, create_personal_log, process_claimed_personal_log
from models import Media, PersonalLog
from storage import storage


def _member(client):
    return client.post('/members', json={'name': 'Drummer', 'instrument': 'Drums'}).get_json()['id']


def _initiate(client, member_id, declared_bytes=64):
    return client.post('/uploads/multipart/initiate', json={
        'filename': 'practice.mp4', 'member_id': member_id, 'declared_bytes': declared_bytes,
        'title': 'Evening practice', 'content_type': 'video/mp4',
    })


def test_personal_log_presign_validates_member_and_target_isolation(client):
    member_id = _member(client)
    valid = client.post('/uploads/presign', json={
        'upload_type': 'personal_log', 'filename': 'take.mp4', 'member_id': member_id,
    })
    assert valid.status_code == 200
    assert valid.get_json()['key'].startswith('personal_logs/')
    missing_member = client.post('/uploads/presign', json={
        'upload_type': 'personal_log', 'filename': 'take.mp4', 'member_id': 999,
    })
    assert missing_member.status_code == 404
    mixed = client.post('/uploads/multipart/initiate', json={
        'filename': 'take.mp4', 'member_id': member_id, 'song_id': 1, 'declared_bytes': 10,
    })
    assert mixed.status_code == 400
    no_target = client.post('/uploads/multipart/initiate', json={
        'filename': 'take.mp4', 'declared_bytes': 10,
    })
    assert no_target.status_code == 400


def test_personal_log_multipart_complete_creates_queued_log(client, app, monkeypatch):
    member_id = _member(client)
    session_id = _initiate(client, member_id, 64).get_json()['session_id']
    assert client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}).status_code == 200
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 64})
    response = client.post(f'/uploads/multipart/{session_id}/complete', json={
        'parts': [{'part_number': 1, 'etag': 'part-one'}],
    })
    assert response.status_code == 201
    log = response.get_json()['personal_log']
    assert log['member_id'] == member_id
    assert log['transcoding_status'] == 'queued'
    repeated = client.post(f'/uploads/multipart/{session_id}/complete', json={'parts': []})
    assert repeated.status_code == 200
    assert repeated.get_json()['personal_log']['id'] == log['id']


def test_single_personal_log_complete_uses_actual_size_and_rejects_mixed_target(client, app, monkeypatch):
    member_id = _member(client)
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 20})
    response = client.post('/uploads/complete/personal-log', json={
        'filename': 'direct.mp4', 'original_filename': 'direct.mp4', 'member_id': member_id,
        'title': 'Direct log', 'file_size': 20,
    })
    assert response.status_code == 201
    assert response.get_json()['transcoding_status'] == 'queued'
    mixed = client.post('/uploads/complete/personal-log', json={
        'filename': 'bad.mp4', 'member_id': member_id, 'song_id': 1,
    })
    assert mixed.status_code == 400


def test_personal_log_worker_lifecycle_and_deletes_audio_derivative(client, app, monkeypatch):
    member_id = _member(client)
    with app.app_context():
        log = create_personal_log(member_id=member_id, title='Worker log', filename='worker.mp4', file_type='video')
        db.session.add(log)
        db.session.commit()
        kind, log_id = claim_next_queued_item()
        assert kind == 'personal_log'
    monkeypatch.setattr('media_processing.extract_m4a_audio', lambda filename, **kwargs: 'worker_audio.m4a')
    monkeypatch.setattr(storage, 'exists', lambda key: True)
    with app.app_context():
        process_claimed_personal_log(log_id, app.config)
        log = db.session.get(PersonalLog, log_id)
        assert log.transcoding_status == 'completed'
        assert log.audio_filename == 'worker_audio.m4a'
    deleted = []
    monkeypatch.setattr(storage, 'delete', lambda key: deleted.append(key))
    assert client.delete(f'/personal-logs/{log_id}').status_code == 200
    assert deleted == ['personal_logs/worker_audio.m4a', 'personal_logs/worker.mp4']


def test_member_deletion_removes_personal_log_original_and_audio(client, app, monkeypatch):
    member_id = _member(client)
    with app.app_context():
        log = create_personal_log(member_id=member_id, title='Old', filename='old.mp4', file_type='video')
        log.audio_filename = 'old_audio.m4a'
        db.session.add(log)
        db.session.commit()
    deleted = []
    monkeypatch.setattr(storage, 'delete', lambda key: deleted.append(key))
    assert client.delete(f'/members/{member_id}').status_code == 200
    assert deleted == ['personal_logs/old_audio.m4a', 'personal_logs/old.mp4']


def test_startup_migration_queues_existing_personal_video_with_default_status(tmp_path):
    db_path = tmp_path / 'legacy.db'
    connection = sqlite3.connect(db_path)
    connection.executescript('''
        CREATE TABLE media (id INTEGER PRIMARY KEY, file_type VARCHAR(20));
        CREATE TABLE personal_log (id INTEGER PRIMARY KEY, file_type VARCHAR(20));
        CREATE TABLE rehearsal (id INTEGER PRIMARY KEY);
        INSERT INTO personal_log (id, file_type) VALUES (1, 'video');
    ''')
    connection.commit()
    connection.close()

    flask_app = Flask(__name__)
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    _run_migrations(flask_app)

    connection = sqlite3.connect(db_path)
    status = connection.execute('SELECT transcoding_status FROM personal_log WHERE id = 1').fetchone()[0]
    connection.close()
    assert status == 'queued'


def test_personal_log_delete_attempts_original_after_audio_failure_and_keeps_row(client, app, monkeypatch):
    member_id = _member(client)
    with app.app_context():
        log = create_personal_log(member_id=member_id, title='Delete', filename='delete.mp4', file_type='video')
        log.audio_filename = 'delete_audio.m4a'
        db.session.add(log)
        db.session.commit()
        log_id = log.id
    attempted = []

    def failing_delete(key):
        attempted.append(key)
        if key.endswith('_audio.m4a'):
            raise RuntimeError('audio unavailable')

    monkeypatch.setattr(storage, 'delete', failing_delete)
    with pytest.raises(RuntimeError):
        client.delete(f'/personal-logs/{log_id}')
    assert attempted == ['personal_logs/delete_audio.m4a', 'personal_logs/delete.mp4']
    with app.app_context():
        assert db.session.get(PersonalLog, log_id) is not None


def test_media_delete_keeps_row_when_original_delete_fails(client, app, monkeypatch):
    song_id = client.post('/songs', json={'title': 'Delete song', 'artist': 'Band'}).get_json()['id']
    with app.app_context():
        media = create_media(song_id=song_id, filename='media.mp4', file_type='video')
        media.audio_filename = 'media_audio.m4a'
        db.session.add(media)
        db.session.commit()
        media_id = media.id
    attempted = []

    def failing_delete(key):
        attempted.append(key)
        if key.endswith('media.mp4'):
            raise RuntimeError('original unavailable')

    monkeypatch.setattr(storage, 'delete', failing_delete)
    with pytest.raises(RuntimeError):
        client.delete(f'/media/{media_id}')
    assert attempted == ['media/media_audio.m4a', 'media/media.mp4']
    with app.app_context():
        assert db.session.get(Media, media_id) is not None
