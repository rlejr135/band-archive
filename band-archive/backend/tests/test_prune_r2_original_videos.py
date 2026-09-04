from extensions import db
from models import Media, Song
from storage import storage
from tools import prune_r2_original_videos as prune


def _media(app, *, audio_filename='clip_audio.m4a'):
    with app.app_context():
        song = Song(title='Prune', artist='Band')
        db.session.add(song)
        db.session.flush()
        media = Media(
            song_id=song.id, filename='clip.mp4', original_filename='clip.mp4', file_type='video',
            transcoding_status='completed', audio_filename=audio_filename,
            video_720_filename='media/transcoded/720/1/source.mp4', video_720_source_etag='source',
        )
        db.session.add(media)
        db.session.commit()
        return media.id


def _verified_heads(monkeypatch):
    objects = {
        'media/clip.mp4': {'ContentLength': 100, 'ETag': '"source"'},
        'media/transcoded/720/1/source.mp4': {'ContentLength': 40, 'ETag': '"derivative"'},
        'media/clip_audio.m4a': {'ContentLength': 5, 'ETag': '"audio"'},
    }
    deleted = []
    monkeypatch.setattr(storage, 'head', lambda key: objects[key])
    monkeypatch.setattr(storage, 'delete', lambda key: (deleted.append(key), objects.pop(key)))
    return objects, deleted


def test_prune_is_dry_run_until_apply_then_marks_only_verified_original(app, monkeypatch):
    media_id = _media(app)
    objects, deleted = _verified_heads(monkeypatch)
    with app.app_context():
        assert prune.prune_originals() == [{'media_id': media_id, 'state': 'eligible'}]
        assert deleted == []
        assert db.session.get(Media, media_id).original_pruned_at is None

        assert prune.prune_originals(apply=True) == [{'media_id': media_id, 'state': 'pruned'}]
        assert deleted == ['media/clip.mp4']
        assert 'media/transcoded/720/1/source.mp4' in objects
        assert 'media/clip_audio.m4a' in objects
        assert db.session.get(Media, media_id).original_pruned_at is not None


def test_prune_blocks_video_without_completed_radio_audio(app, monkeypatch):
    media_id = _media(app, audio_filename=None)
    _verified_heads(monkeypatch)
    with app.app_context():
        assert prune.prune_originals(apply=True) == [{
            'media_id': media_id, 'state': 'blocked', 'reason': 'audio_not_completed',
        }]
        assert db.session.get(Media, media_id).original_pruned_at is None
