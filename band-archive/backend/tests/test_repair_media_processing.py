from pathlib import Path

from extensions import db
from media_processing import create_media
from models import Media
from repair_media_processing import repair_media_processing


class FakeStorage:
    def __init__(self, keys):
        self.keys = set(keys)

    def exists(self, key):
        return key in self.keys


def test_repair_is_dry_run_then_idempotent_enqueue(app):
    with app.app_context():
        complete = create_media(song_id=1, filename='complete.mp4', file_type='video')
        complete.transcoding_status = 'pending'
        queued = create_media(song_id=1, filename='queued.mp4', file_type='video')
        queued.transcoding_status = 'completed'
        non_video = create_media(song_id=1, filename='image.jpg', file_type='image')
        non_video.transcoding_status = 'pending'
        missing = create_media(song_id=1, filename='missing.mp4', file_type='video')
        db.session.add_all([complete, queued, non_video, missing])
        db.session.commit()
        storage = FakeStorage({
            'media/complete.mp4', 'media/complete_audio.m4a', 'media/queued.mp4',
        })
        dry_run = repair_media_processing(app, storage_client=storage)
        assert dry_run['would_change'] == 4
        assert db.session.get(Media, complete.id).transcoding_status == 'pending'
        applied = repair_media_processing(app, enqueue=True, storage_client=storage)
        assert applied['changed'] == 4
        db.session.expire_all()
        assert db.session.get(Media, complete.id).transcoding_status == 'completed'
        assert db.session.get(Media, complete.id).audio_filename == 'complete_audio.m4a'
        assert db.session.get(Media, queued.id).transcoding_status == 'queued'
        assert db.session.get(Media, non_video.id).transcoding_status == 'not_required'
        assert db.session.get(Media, missing.id).transcoding_status == 'failed'
        repeat = repair_media_processing(app, enqueue=True, storage_client=storage)
        assert repeat['changed'] == 0


def test_repair_limit_and_storage_failure_are_reported(app):
    with app.app_context():
        db.session.add_all([
            create_media(song_id=1, filename='one.mp4', file_type='video'),
            create_media(song_id=1, filename='two.mp4', file_type='video'),
        ])
        db.session.commit()

        class BrokenStorage:
            def exists(self, key):
                raise RuntimeError('offline')

        summary = repair_media_processing(app, limit=1, storage_client=BrokenStorage())
        assert summary['examined'] == 1
        assert summary['storage_errors'] == 1


def test_repair_limit_ignores_preceding_normal_non_video_rows(app):
    with app.app_context():
        db.session.add_all([
            create_media(song_id=1, filename='normal-a.jpg', file_type='image'),
            create_media(song_id=1, filename='normal-b.mp3', file_type='audio'),
            create_media(song_id=1, filename='first.mp4', file_type='video'),
            create_media(song_id=1, filename='second.mp4', file_type='video'),
        ])
        db.session.commit()
        storage = FakeStorage({'media/first.mp4', 'media/second.mp4'})
        summary = repair_media_processing(app, limit=2, storage_client=storage)
        assert summary['examined'] == 2
        assert summary['queued'] == 2


def test_fly_keeps_one_machine_running_for_the_audio_worker():
    config = Path(__file__).parents[1] / 'fly.toml'
    content = config.read_text(encoding='utf-8')
    assert "auto_stop_machines = 'off'" in content
    assert 'min_machines_running = 1' in content
