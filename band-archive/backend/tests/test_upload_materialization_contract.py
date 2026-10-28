"""Materialization callbacks preserve fields, evaluation and save boundaries.

These focused doubles complement the real database recovery and upload tests;
they do not establish authorization or PostgreSQL lock behavior.
"""

from types import SimpleNamespace

from flask import Flask
import pytest

from errors import ValidationError
import media_processing
import routes.uploads as materialization


CASES = [('direct', 'media'), ('direct', 'personal_log'),
         ('multipart', 'media'), ('multipart', 'personal_log')]


def _callback(mode):
    return (materialization._v2_direct_item if mode == 'direct'
            else materialization._v2_multipart_item)


class UploadSession:
    def __init__(self, events, kind, filename='take.mp4'):
        self.events = events
        self._kind = kind
        self._filename = filename
        self.band_id = 7
        self.created_by_membership_id = 13
        self.original_filename = 'Original take.mp4'
        self.song_id = 21
        self.rehearsal_id = 22
        self.member_id = 23
        self.title = 'Practice'

    @property
    def object_key(self):
        self.events.append('object_key')
        return f'bands/7/media/{self._filename}'

    @property
    def kind(self):
        self.events.append('kind')
        return self._kind


def _install(monkeypatch, events, kind):
    app = Flask(__name__)
    band = SimpleNamespace(default_visibility='MEMBERS')

    def get(model, identity):
        assert model is materialization.Band and identity == 7
        events.append('band_get')
        return band

    def visibility(session, fallback):
        assert fallback == 'MEMBERS'
        events.append('visibility')
        return 'PUBLIC'

    def spec(session):
        events.append('spec')
        return (materialization.PERSONAL_LOG_TARGET if kind == 'personal_log'
                else materialization.MEDIA_TARGET)

    captured = {}

    def create(**fields):
        events.append('create')
        captured.update(fields)
        return SimpleNamespace(**fields)

    def save(actual_app, record):
        assert actual_app is app
        events.append('save')
        return record

    monkeypatch.setattr(materialization, 'db', SimpleNamespace(session=SimpleNamespace(get=get)))
    monkeypatch.setattr(materialization, 'upload_session_visibility', visibility)
    monkeypatch.setattr(materialization, 'spec_for_session', spec)
    monkeypatch.setattr(materialization, 'create_media', create)
    monkeypatch.setattr(materialization, 'create_personal_log', create)
    monkeypatch.setattr(materialization, 'save_media_and_start', save)
    monkeypatch.setattr(materialization, 'save_personal_log_and_start', save)
    return app, captured


@pytest.mark.parametrize('mode,kind', CASES)
def test_materialization_keeps_target_fields_and_evaluation_order(monkeypatch, mode, kind):
    events = []
    app, captured = _install(monkeypatch, events, kind)
    session = UploadSession(events, kind)
    with app.app_context():
        item = _callback(mode)(session, 123)
    expected = dict(filename='take.mp4', original_filename='Original take.mp4',
                    file_type='video', file_size=123, band_id=7,
                    created_by_membership_id=13, visibility='PUBLIC',
                    object_key='bands/7/media/take.mp4')
    expected.update(dict(member_id=23, title='Practice') if kind == 'personal_log'
                    else dict(song_id=21, rehearsal_id=22))
    assert vars(item) == captured == expected
    prefix = (['object_key', 'band_get', 'spec', 'visibility', 'object_key']
              if mode == 'multipart' else
              ['band_get', 'object_key', 'visibility', 'object_key', 'kind'])
    assert events == prefix + ['create', 'save']


@pytest.mark.parametrize('mode,kind', CASES)
def test_materialization_rejects_oversize_video_before_create_or_save(monkeypatch, mode, kind):
    events = []
    app, _ = _install(monkeypatch, events, kind)
    with app.app_context(), pytest.raises(ValidationError, match='1 GiB'):
        _callback(mode)(UploadSession(events, kind), 1024 * 1024 * 1024 + 1)
    assert 'create' not in events and 'save' not in events


@pytest.mark.parametrize('mode,kind', CASES)
def test_materialization_audio_does_not_apply_video_limit_and_normalizes_empty_original(
        monkeypatch, mode, kind):
    events = []
    app, captured = _install(monkeypatch, events, kind)
    session = UploadSession(events, kind, 'take.mp3')
    session.original_filename = ''
    with app.app_context():
        _callback(mode)(session, 1024 * 1024 * 1024 + 1)
    assert captured['file_type'] == 'audio'
    assert captured['original_filename'] is None


def test_gallery_materialization_adds_and_flushes_without_commit(monkeypatch):
    events = []
    app, _ = _install(monkeypatch, events, 'gallery')
    captured = {}

    def image(**fields):
        events.append('create_gallery')
        captured.update(fields)
        return SimpleNamespace(**fields)

    monkeypatch.setattr(materialization, 'GalleryImage', image)
    materialization.db.session.add = lambda item: events.append('add')
    materialization.db.session.flush = lambda: events.append('flush')
    materialization.db.session.commit = lambda: pytest.fail('gallery callback must not commit')
    with app.app_context():
        result = materialization._v2_direct_gallery_item(UploadSession(events, 'gallery', 'pic.jpg'), 9)
    assert vars(result) == captured == dict(band_id=7, created_by_membership_id=13,
        visibility='PUBLIC', filename='pic.jpg', original_filename='Original take.mp4',
        object_key='bands/7/media/pic.jpg', file_size=9)
    assert events == ['band_get', 'visibility', 'object_key', 'object_key', 'create_gallery', 'add', 'flush']


@pytest.mark.parametrize('file_type', ['video', 'audio'])
def test_processing_save_commits_before_video_worker_wakeup(monkeypatch, file_type):
    events = []
    record = SimpleNamespace(file_type=file_type, id=5)
    monkeypatch.setattr(media_processing, 'db', SimpleNamespace(session=SimpleNamespace(
        add=lambda value: events.append(('add', value)), commit=lambda: events.append('commit'))))
    monkeypatch.setattr(media_processing, 'start_audio_processing',
                        lambda app, identity: events.append(('wake', app, identity)))
    assert media_processing.save_media_and_start('app', record) is record
    assert events == [('add', record), 'commit'] + ([('wake', 'app', 5)] if file_type == 'video' else [])
