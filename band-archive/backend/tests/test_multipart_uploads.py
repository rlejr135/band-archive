from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

from botocore.exceptions import ClientError
from app import create_app
from config import TestingConfig
from extensions import db
from models import Band, Media, MultipartUploadSession, Song, TenantBackfillState
from routes.uploads import MAX_VIDEO_BYTES, MULTIPART_PART_SIZE, recover_multipart_upload_sessions
from storage import storage


def _song(client):
    return client.post('/songs', json={'title': 'Upload song', 'artist': 'Band'}).get_json()['id']


def _initiate(client, song_id, declared_bytes=32):
    return client.post('/uploads/multipart/initiate', json={
        'filename': 'concert.mp4', 'content_type': 'video/mp4',
        'declared_bytes': declared_bytes, 'song_id': song_id,
    })


def _session(client, song_id, declared_bytes=32):
    data = _initiate(client, song_id, declared_bytes).get_json()
    return data, {data['capability_header']: data['upload_capability_token']}


def _issue_and_ack(client, session_id, headers, part_number=1, bytes=32, etag='etag-1'):
    assert client.post(
        f'/uploads/multipart/{session_id}/parts', json={'part_number': part_number}, headers=headers,
    ).status_code == 200
    return client.post(
        f'/uploads/multipart/{session_id}/parts/{part_number}/ack',
        json={'etag': etag, 'bytes': bytes}, headers=headers,
    )


def test_initiate_returns_opaque_session_and_validates_video_size(client, app):
    song_id = _song(client)
    too_large = _initiate(client, song_id, MAX_VIDEO_BYTES + 1)
    assert too_large.status_code == 400
    response = _initiate(client, song_id)
    assert response.status_code == 201
    data = response.get_json()
    assert set(data) == {
        'session_id', 'filename', 'part_size', 'max_parts', 'expires_at',
        'upload_capability_token', 'capability_header',
    }
    assert data['part_size'] == MULTIPART_PART_SIZE
    assert 16 * 1024 * 1024 <= data['part_size'] <= 32 * 1024 * 1024
    with app.app_context():
        session = db.session.get(MultipartUploadSession, data['session_id'])
        assert session is not None
        assert session.r2_upload_id == 'mock-upload-id'
        assert session.object_key == f"media/{data['filename']}"
        assert session.capability_token_hash != data['upload_capability_token']


def test_part_urls_are_session_scoped_reissuable_and_capability_protected(client):
    song_id = _song(client)
    session, headers = _session(client, song_id)
    session_id = session['session_id']
    assert client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}).status_code == 403
    first = client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}, headers=headers)
    assert first.status_code == 200
    assert first.get_json()['upload_url'].endswith('/1')
    assert client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}, headers=headers).status_code == 200
    ack = client.post(f'/uploads/multipart/{session_id}/parts/1/ack', json={'etag': 'etag-1', 'bytes': 32}, headers=headers)
    assert ack.status_code == 200
    assert client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}, headers=headers).status_code == 409
    assert client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 10001}, headers=headers).status_code == 400
    assert client.post('/uploads/multipart/not-a-real-session/parts', json={'part_number': 1}, headers=headers).status_code == 404


def test_multipart_complete_verifies_actual_size_and_queues_video(client, app, monkeypatch):
    song_id = _song(client)
    session, headers = _session(client, song_id, declared_bytes=123)
    session_id = session['session_id']
    _issue_and_ack(client, session_id, headers, bytes=123)
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 123})
    started = []
    monkeypatch.setattr('media_processing.start_audio_processing', lambda app, media_id: started.append(media_id))
    response = client.post(f'/uploads/multipart/{session_id}/complete', headers=headers)
    assert response.status_code == 201
    media = response.get_json()['media']
    assert media['file_size'] == 123
    assert media['transcoding_status'] == 'queued'
    assert started == [media['id']]
    repeated = client.post(f'/uploads/multipart/{session_id}/complete', headers=headers)
    assert repeated.status_code == 200
    assert repeated.get_json()['media']['id'] == media['id']
    with app.app_context():
        assert Media.query.count() == 1


def test_multipart_complete_rejects_size_mismatch_without_media(client, app, monkeypatch):
    song_id = _song(client)
    session, headers = _session(client, song_id, declared_bytes=50)
    session_id = session['session_id']
    _issue_and_ack(client, session_id, headers, bytes=50)
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 49})
    response = client.post(f'/uploads/multipart/{session_id}/complete', headers=headers)
    assert response.status_code == 400
    with app.app_context():
        assert Media.query.count() == 0
        assert db.session.get(MultipartUploadSession, session_id).status == 'failed'


def test_complete_uses_acknowledged_parts_and_rejects_missing_or_conflicting_ack(client):
    song_id = _song(client)
    session, headers = _session(client, song_id)
    session_id = session['session_id']
    assert client.post(f'/uploads/multipart/{session_id}/complete', headers=headers).status_code == 409
    assert client.post(f'/uploads/multipart/{session_id}/parts/1/ack', json={'etag': 'x', 'bytes': 32}, headers=headers).status_code == 409
    assert client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}, headers=headers).status_code == 200
    first_ack = client.post(f'/uploads/multipart/{session_id}/parts/1/ack', json={'etag': 'x', 'bytes': 32}, headers=headers)
    assert first_ack.status_code == 200
    assert client.post(f'/uploads/multipart/{session_id}/parts/1/ack', json={'etag': 'x', 'bytes': 32}, headers=headers).status_code == 200
    assert client.post(f'/uploads/multipart/{session_id}/parts/1/ack', json={'etag': 'changed', 'bytes': 32}, headers=headers).status_code == 409


def test_abort_is_idempotent_and_expired_session_rejects_parts(client, app, monkeypatch):
    song_id = _song(client)
    session, headers = _session(client, song_id)
    session_id = session['session_id']
    aborted = []
    monkeypatch.setattr(storage, 'abort_multipart_upload', lambda key, upload_id: aborted.append((key, upload_id)))
    assert client.post(f'/uploads/multipart/{session_id}/abort', headers=headers).status_code == 200
    assert client.post(f'/uploads/multipart/{session_id}/abort', headers=headers).status_code == 200
    assert len(aborted) == 1
    expiring, expiring_headers = _session(client, song_id)
    expiring_id = expiring['session_id']
    with app.app_context():
        session = db.session.get(MultipartUploadSession, expiring_id)
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
    expired = client.post(f'/uploads/multipart/{expiring_id}/parts', json={'part_number': 1}, headers=expiring_headers)
    assert expired.status_code == 409
    assert client.post(f'/uploads/multipart/{expiring_id}/abort', headers=expiring_headers).get_json()['status'] == 'expired'


def test_failed_r2_abort_restores_the_retryable_initiated_state(client, app, monkeypatch):
    song_id = _song(client)
    session, headers = _session(client, song_id)
    monkeypatch.setattr(storage, 'abort_multipart_upload',
                        lambda *args: (_ for _ in ()).throw(RuntimeError('temporary R2 failure')))
    assert client.post(f"/uploads/multipart/{session['session_id']}/abort", headers=headers).status_code == 400
    with app.app_context():
        assert db.session.get(MultipartUploadSession, session['session_id']).status == 'initiated'
    monkeypatch.setattr(storage, 'abort_multipart_upload', lambda *args: None)
    assert client.post(f"/uploads/multipart/{session['session_id']}/abort", headers=headers).status_code == 200


def test_startup_recovers_aborting_sessions_without_reviving_their_upload_ids(client, app, monkeypatch):
    song_id = _song(client)
    session, _headers = _session(client, song_id)
    with app.app_context():
        db.session.get(MultipartUploadSession, session['session_id']).status = 'aborting'
        db.session.commit()
    calls = []
    monkeypatch.setattr(storage, 'abort_multipart_upload', lambda key, upload_id: calls.append((key, upload_id)))
    recover_multipart_upload_sessions(app)
    with app.app_context():
        assert db.session.get(MultipartUploadSession, session['session_id']).status == 'aborted'
    assert calls == [(f"media/{session['filename']}", 'mock-upload-id')]

    second, _headers = _session(client, song_id)
    with app.app_context():
        db.session.get(MultipartUploadSession, second['session_id']).status = 'aborting'
        db.session.commit()
    monkeypatch.setattr(storage, 'abort_multipart_upload', lambda *args: (_ for _ in ()).throw(
        ClientError({'Error': {'Code': 'NoSuchUpload'}}, 'AbortMultipartUpload')
    ))
    recover_multipart_upload_sessions(app)
    with app.app_context():
        assert db.session.get(MultipartUploadSession, second['session_id']).status == 'aborted'


def test_session_status_returns_target_acknowledgements_and_rejects_wrong_token(client):
    song_id = _song(client)
    session, headers = _session(client, song_id, declared_bytes=32)
    session_id = session['session_id']
    assert client.get(f'/uploads/multipart/{session_id}').status_code == 403
    assert client.get(f'/uploads/multipart/{session_id}', headers={'X-Upload-Capability': 'wrong'}).status_code == 403
    assert client.post(f'/uploads/multipart/{session_id}/parts/1/ack', json={'etag': 'x', 'bytes': 32}).status_code == 403
    assert client.post(f'/uploads/multipart/{session_id}/complete').status_code == 403
    assert client.post(f'/uploads/multipart/{session_id}/abort').status_code == 403
    _issue_and_ack(client, session_id, headers, bytes=32)
    response = client.get(f'/uploads/multipart/{session_id}', headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data['target'] == {'kind': 'media', 'song_id': song_id, 'rehearsal_id': None}
    assert data['parts'][0]['status'] == 'acknowledged'
    assert data['parts'][0]['etag'] == 'etag-1'
    assert session['upload_capability_token'] not in response.get_data(as_text=True)


def test_interrupted_or_timed_out_completion_can_resume_from_stored_acknowledgements(client, app, monkeypatch):
    song_id = _song(client)
    session, headers = _session(client, song_id, declared_bytes=32)
    session_id = session['session_id']
    _issue_and_ack(client, session_id, headers, bytes=32)
    with app.app_context():
        stored = db.session.get(MultipartUploadSession, session_id)
        stored.status = 'completing'
        db.session.commit()
        recover_multipart_upload_sessions(app)
        assert db.session.get(MultipartUploadSession, session_id).status == 'initiated'
    monkeypatch.setattr(storage, 'complete_multipart_upload', lambda *args: (_ for _ in ()).throw(TimeoutError('lost response')))
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 32})
    response = client.post(f'/uploads/multipart/{session_id}/complete', headers=headers)
    assert response.status_code == 201
    assert response.get_json()['media']['transcoding_status'] == 'queued'


def test_single_complete_uses_head_size_not_client_metadata(client, app, monkeypatch):
    song_id = _song(client)
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 10})
    mismatch = client.post('/uploads/complete/media', json={
        'filename': 'single.mp4', 'original_filename': 'single.mp4', 'song_id': song_id, 'file_size': 9,
    })
    assert mismatch.status_code == 400
    with app.app_context():
        assert Media.query.count() == 0


def test_file_sqlite_abort_claim_blocks_a_racing_complete(tmp_path, monkeypatch):
    """One atomic claim wins even when independent clients overlap at R2."""
    db_path = tmp_path / 'abort-complete-race.db'

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path.as_posix()}'

    app = create_app(FileConfig, start_worker=False)
    try:
        with app.app_context():
            db.drop_all(); db.create_all()
            band = Band(name='legacy', slug='legacy-race', default_visibility='PUBLIC',
                        storage_quota_bytes=1024, is_legacy_import=True)
            db.session.add(band); db.session.flush()
            db.session.add(TenantBackfillState(key='phase4_tenant_scope', status='validated'))
            song = Song(band_id=band.id, visibility='PUBLIC', title='race', artist='band')
            db.session.add(song); db.session.commit()
            song_id = song.id
        with app.test_client() as setup_client:
            started = _initiate(setup_client, song_id, declared_bytes=32).get_json()
            headers = {started['capability_header']: started['upload_capability_token']}
            _issue_and_ack(setup_client, started['session_id'], headers, bytes=32)

        abort_entered, release_abort = Event(), Event()
        r2_calls = []

        def abort_r2(key, upload_id):
            r2_calls.append(('abort', key))
            abort_entered.set()
            assert release_abort.wait(5)

        monkeypatch.setattr(storage, 'abort_multipart_upload', abort_r2)
        monkeypatch.setattr(storage, 'complete_multipart_upload',
                            lambda *args: r2_calls.append(('complete', args[0])))
        monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 32})

        def abort_request():
            with app.test_client() as race_client:
                return race_client.post(f"/uploads/multipart/{started['session_id']}/abort", headers=headers).status_code

        def complete_request():
            with app.test_client() as race_client:
                return race_client.post(f"/uploads/multipart/{started['session_id']}/complete", headers=headers).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            abort_future = pool.submit(abort_request)
            assert abort_entered.wait(5)
            complete_future = pool.submit(complete_request)
            assert complete_future.result(timeout=5) == 409
            release_abort.set()
            assert abort_future.result(timeout=5) == 200
        assert r2_calls == [('abort', f"media/{started['filename']}")]
        with app.app_context():
            session = db.session.get(MultipartUploadSession, started['session_id'])
            assert session.status == 'aborted'
            assert Media.query.count() == 0
    finally:
        with app.app_context():
            db.session.remove(); db.drop_all()


def test_file_sqlite_complete_claim_blocks_a_racing_abort(tmp_path, monkeypatch):
    """The opposite winner also leaves one terminal operation and one 409."""
    db_path = tmp_path / 'complete-abort-race.db'

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path.as_posix()}'

    app = create_app(FileConfig, start_worker=False)
    try:
        with app.app_context():
            db.drop_all(); db.create_all()
            band = Band(name='legacy complete', slug='legacy-complete-race', default_visibility='PUBLIC',
                        storage_quota_bytes=1024, is_legacy_import=True)
            db.session.add(band); db.session.flush()
            db.session.add(TenantBackfillState(key='phase4_tenant_scope', status='validated'))
            song = Song(band_id=band.id, visibility='PUBLIC', title='complete race', artist='band')
            db.session.add(song); db.session.commit(); song_id = song.id
        with app.test_client() as setup_client:
            started = _initiate(setup_client, song_id, declared_bytes=32).get_json()
            headers = {started['capability_header']: started['upload_capability_token']}
            _issue_and_ack(setup_client, started['session_id'], headers, bytes=32)

        complete_entered, release_complete = Event(), Event()
        r2_calls = []

        def complete_r2(key, upload_id, parts):
            r2_calls.append(('complete', key))
            complete_entered.set()
            assert release_complete.wait(5)

        monkeypatch.setattr(storage, 'complete_multipart_upload', complete_r2)
        monkeypatch.setattr(storage, 'abort_multipart_upload',
                            lambda key, upload_id: r2_calls.append(('abort', key)))
        monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 32})

        def complete_request():
            with app.test_client() as race_client:
                return race_client.post(f"/uploads/multipart/{started['session_id']}/complete", headers=headers).status_code

        def abort_request():
            with app.test_client() as race_client:
                return race_client.post(f"/uploads/multipart/{started['session_id']}/abort", headers=headers).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            complete_future = pool.submit(complete_request)
            assert complete_entered.wait(5)
            abort_future = pool.submit(abort_request)
            assert abort_future.result(timeout=5) == 409
            release_complete.set()
            assert complete_future.result(timeout=5) == 201
        assert r2_calls == [('complete', f"media/{started['filename']}")]
        with app.app_context():
            assert db.session.get(MultipartUploadSession, started['session_id']).status == 'completed'
            assert Media.query.count() == 1
    finally:
        with app.app_context():
            db.session.remove(); db.drop_all()
