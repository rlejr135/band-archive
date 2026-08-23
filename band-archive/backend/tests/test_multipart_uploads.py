from datetime import datetime, timedelta, timezone

from extensions import db
from models import Media, MultipartUploadSession
from routes.uploads import MAX_VIDEO_BYTES, MULTIPART_PART_SIZE
from storage import storage


def _song(client):
    return client.post('/songs', json={'title': 'Upload song', 'artist': 'Band'}).get_json()['id']


def _initiate(client, song_id, declared_bytes=32):
    return client.post('/uploads/multipart/initiate', json={
        'filename': 'concert.mp4', 'content_type': 'video/mp4',
        'declared_bytes': declared_bytes, 'song_id': song_id,
    })


def test_initiate_returns_opaque_session_and_validates_video_size(client, app):
    song_id = _song(client)
    too_large = _initiate(client, song_id, MAX_VIDEO_BYTES + 1)
    assert too_large.status_code == 400
    response = _initiate(client, song_id)
    assert response.status_code == 201
    data = response.get_json()
    assert set(data) == {'session_id', 'filename', 'part_size', 'max_parts', 'expires_at'}
    assert data['part_size'] == MULTIPART_PART_SIZE
    assert 16 * 1024 * 1024 <= data['part_size'] <= 32 * 1024 * 1024
    with app.app_context():
        session = db.session.get(MultipartUploadSession, data['session_id'])
        assert session is not None
        assert session.r2_upload_id == 'mock-upload-id'
        assert session.object_key == f"media/{data['filename']}"


def test_part_urls_are_session_scoped_and_reject_tampering(client):
    song_id = _song(client)
    session_id = _initiate(client, song_id).get_json()['session_id']
    first = client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1})
    assert first.status_code == 200
    assert first.get_json()['upload_url'].endswith('/1')
    assert client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}).status_code == 409
    assert client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 10001}).status_code == 400
    assert client.post('/uploads/multipart/not-a-real-session/parts', json={'part_number': 1}).status_code == 404


def test_multipart_complete_verifies_actual_size_and_queues_video(client, app, monkeypatch):
    song_id = _song(client)
    session_id = _initiate(client, song_id, declared_bytes=123).get_json()['session_id']
    client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1})
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 123})
    started = []
    monkeypatch.setattr('media_processing.start_audio_processing', lambda app, media_id: started.append(media_id))
    response = client.post(f'/uploads/multipart/{session_id}/complete', json={
        'parts': [{'part_number': 1, 'etag': 'etag-1'}],
    })
    assert response.status_code == 201
    media = response.get_json()['media']
    assert media['file_size'] == 123
    assert media['transcoding_status'] == 'queued'
    assert started == [media['id']]
    repeated = client.post(f'/uploads/multipart/{session_id}/complete', json={'parts': []})
    assert repeated.status_code == 200
    assert repeated.get_json()['media']['id'] == media['id']
    with app.app_context():
        assert Media.query.count() == 1


def test_multipart_complete_rejects_size_mismatch_without_media(client, app, monkeypatch):
    song_id = _song(client)
    session_id = _initiate(client, song_id, declared_bytes=50).get_json()['session_id']
    client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1})
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 49})
    response = client.post(f'/uploads/multipart/{session_id}/complete', json={
        'parts': [{'part_number': 1, 'etag': 'etag-1'}],
    })
    assert response.status_code == 400
    with app.app_context():
        assert Media.query.count() == 0
        assert db.session.get(MultipartUploadSession, session_id).status == 'failed'


def test_complete_rejects_missing_etag_unissued_and_duplicate_parts(client):
    song_id = _song(client)
    session_id = _initiate(client, song_id).get_json()['session_id']
    client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1})
    missing_etag = client.post(f'/uploads/multipart/{session_id}/complete', json={
        'parts': [{'part_number': 1}],
    })
    assert missing_etag.status_code == 400
    unissued = client.post(f'/uploads/multipart/{session_id}/complete', json={
        'parts': [{'part_number': 2, 'etag': 'x'}],
    })
    assert unissued.status_code == 400
    duplicate = client.post(f'/uploads/multipart/{session_id}/complete', json={
        'parts': [{'part_number': 1, 'etag': 'x'}, {'part_number': 1, 'etag': 'y'}],
    })
    assert duplicate.status_code == 400


def test_abort_is_idempotent_and_expired_session_rejects_parts(client, app, monkeypatch):
    song_id = _song(client)
    session_id = _initiate(client, song_id).get_json()['session_id']
    aborted = []
    monkeypatch.setattr(storage, 'abort_multipart_upload', lambda key, upload_id: aborted.append((key, upload_id)))
    assert client.post(f'/uploads/multipart/{session_id}/abort').status_code == 200
    assert client.post(f'/uploads/multipart/{session_id}/abort').status_code == 200
    assert len(aborted) == 1
    expiring_id = _initiate(client, song_id).get_json()['session_id']
    with app.app_context():
        session = db.session.get(MultipartUploadSession, expiring_id)
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
    expired = client.post(f'/uploads/multipart/{expiring_id}/parts', json={'part_number': 1})
    assert expired.status_code == 409


def test_single_complete_uses_head_size_not_client_metadata(client, app, monkeypatch):
    song_id = _song(client)
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    mismatch = client.post('/uploads/complete/media', json={
        'filename': 'single.mp4', 'original_filename': 'single.mp4', 'song_id': song_id, 'file_size': 9,
    })
    assert mismatch.status_code == 400
    with app.app_context():
        assert Media.query.count() == 0
