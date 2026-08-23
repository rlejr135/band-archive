import io
import subprocess
from datetime import datetime, timezone

import pytest

from extensions import db
from media_processing import (
    AudioProcessingError,
    create_media,
    extract_m4a_audio,
    _process_audio_job,
)
from models import Media
from storage import storage


def _song(client):
    return client.post('/songs', json={'title': 'Song', 'artist': 'Artist'}).get_json()['id']


def test_non_video_media_is_never_pending(app):
    with app.app_context():
        for file_type in ('audio', 'image', 'document'):
            media = create_media(song_id=1, filename=f'{file_type}.bin', file_type=file_type)
            assert media.transcoding_status == 'not_required'


@pytest.mark.parametrize('filename', ['clip.mp4', 'photo.jpg', 'score.pdf'])
def test_presigned_completion_initializes_consistent_status(client, monkeypatch, filename):
    song_id = _song(client)
    started = []
    monkeypatch.setattr('media_processing.start_audio_processing', lambda app, media_id: started.append(media_id))
    response = client.post('/uploads/complete/media', json={
        'filename': filename, 'original_filename': filename, 'song_id': song_id,
    })
    assert response.status_code == 201
    payload = response.get_json()
    expected = 'queued' if filename.endswith('.mp4') else 'not_required'
    assert payload['transcoding_status'] == expected
    assert started == ([payload['id']] if expected == 'queued' else [])


def test_song_direct_upload_starts_video_lifecycle(client, monkeypatch):
    song_id = _song(client)
    started = []
    monkeypatch.setattr('media_processing.start_audio_processing', lambda app, media_id: started.append(media_id))
    response = client.post(
        f'/songs/{song_id}/media', data={'file': (io.BytesIO(b'video'), 'clip.mp4')},
        content_type='multipart/form-data',
    )
    assert response.status_code == 201
    assert response.get_json()['transcoding_status'] == 'queued'
    assert started == [response.get_json()['id']]


def test_rehearsal_direct_upload_starts_video_lifecycle(client, monkeypatch):
    song_id = _song(client)
    rehearsal = client.post('/rehearsals', json={'title': 'Practice', 'date': '2026-01-01'}).get_json()
    started = []
    monkeypatch.setattr('media_processing.start_audio_processing', lambda app, media_id: started.append(media_id))
    response = client.post(
        f"/rehearsals/{rehearsal['id']}/media",
        data={'song_id': str(song_id), 'file': (io.BytesIO(b'video'), 'clip.mp4')},
        content_type='multipart/form-data',
    )
    assert response.status_code == 201
    assert response.get_json()['transcoding_status'] == 'queued'
    assert started == [response.get_json()['id']]


def test_m4a_extraction_uses_aac_copy_and_only_uploads_audio(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == 'ffprobe':
            return subprocess.CompletedProcess(args, 0, stdout='aac\n')
        with open(args[-1], 'wb') as output:
            output.write(b'm4a')
        return subprocess.CompletedProcess(args, 0, stdout='')

    uploaded = []
    monkeypatch.setattr('media_processing.subprocess.run', fake_run)
    monkeypatch.setattr(storage, 'upload', lambda key, file_obj, content_type=None: uploaded.append((key, content_type)))
    result = extract_m4a_audio('clip.mp4')
    assert result == 'clip_audio.m4a'
    assert uploaded == [('media/clip_audio.m4a', 'audio/mp4')]
    ffmpeg_call = next(call for call in calls if call[0] == 'ffmpeg')
    assert ['-c:a', 'copy'] == ffmpeg_call[ffmpeg_call.index('-c:a'):ffmpeg_call.index('-c:a') + 2]
    assert not any('720' in value or '480' in value for call in calls for value in call)


def test_timeout_marks_video_failed_with_safe_error(app, monkeypatch):
    with app.app_context():
        media = create_media(song_id=1, filename='clip.mp4', file_type='video')
        db.session.add(media)
        db.session.commit()
        media_id = media.id
    monkeypatch.setattr('media_processing.extract_m4a_audio', lambda filename: (_ for _ in ()).throw(AudioProcessingError('timeout')))
    _process_audio_job(app, media_id)
    with app.app_context():
        media = db.session.get(Media, media_id)
        assert media.transcoding_status == 'failed'
        assert media.processing_error == 'Audio processing timed out.'
        assert media.processing_started_at is not None
        assert media.processing_completed_at is not None


def test_completed_video_has_only_original_and_audio_urls(app):
    with app.app_context():
        media = create_media(song_id=1, filename='clip.mp4', file_type='video')
        media.transcoding_status = 'completed'
        media.audio_filename = 'clip_audio.m4a'
        payload = media.to_dict()
    assert payload['audio_url'].endswith('clip_audio.m4a')
    assert payload['qualities'] == {
        'original': 'https://storage.test/media/clip.mp4',
        'audio': 'https://storage.test/media/clip_audio.m4a',
    }


def test_processing_status_and_retry_api(client, app, monkeypatch):
    with app.app_context():
        media = create_media(song_id=_song(client), filename='clip.mp4', file_type='video')
        media.transcoding_status = 'failed'
        media.processing_error = 'Audio processing failed.'
        db.session.add(media)
        db.session.commit()
        media_id = media.id
    started = []
    monkeypatch.setattr('media_processing.start_audio_processing', lambda app, media_id: started.append(media_id))
    status = client.get(f'/media/{media_id}/processing')
    assert status.status_code == 200
    assert status.get_json()['status'] == 'failed'
    retry = client.post(f'/media/{media_id}/retry-audio')
    assert retry.status_code == 202
    assert retry.get_json()['status'] == 'queued'
    assert started == [media_id]
    duplicate = client.post(f'/media/{media_id}/retry-audio')
    assert duplicate.status_code == 409


def test_retry_rejects_non_video(client, app):
    with app.app_context():
        media = create_media(song_id=_song(client), filename='song.mp3', file_type='audio')
        db.session.add(media)
        db.session.commit()
        media_id = media.id
    response = client.post(f'/media/{media_id}/retry-audio')
    assert response.status_code == 400
