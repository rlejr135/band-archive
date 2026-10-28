"""HTTP completion envelopes before introducing an explicit v2 adapter.

Only completion orchestration/serialization is stubbed. Existing upload tests
retain real session state-machine, authorization, R2 and database coverage.
"""

from types import SimpleNamespace

import pytest
from flask import Flask

import multipart_upload_service
import routes.uploads as uploads


CASES = [
    ('direct', 'media', 'media'),
    ('direct', 'personal_log', 'personal_log'),
    ('gallery', 'gallery', 'gallery'),
    ('multipart', 'media', 'media'),
    ('multipart', 'personal_log', 'personal_log'),
]
CASE_IDS = ['direct-media', 'direct-personal-log', 'direct-gallery',
            'multipart-media', 'multipart-personal-log']


class ResultItem:
    def __init__(self, events, *, truth=True, fail=False):
        self.events = events
        self.truth = truth
        self.fail = fail

    def __bool__(self):
        self.events.append('item_truth')
        return self.truth

    def to_dict(self):
        self.events.append('serialize')
        if self.fail:
            raise RuntimeError('serialization failed after commit')
        return {'id': 42, 'filename': 'original.mp4', 'transcoding_status': 'queued',
                'url': None, 'audio_url': None, 'legacy_only': True}


class ReloadedSession:
    def __init__(self, events, kind):
        self.events = events
        self._kind = kind

    @property
    def id(self):
        self.events.append('session_id')
        return 'opaque-session'

    @property
    def kind(self):
        self.events.append('session_kind')
        return self._kind


def _client(monkeypatch, events, mode, kind, result_key, item, status):
    app = Flask(__name__)
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True)
    app.register_blueprint(uploads.uploads_bp)
    initial = SimpleNamespace(kind=kind)
    reloaded = ReloadedSession(events, kind)

    def creator(*_args, **_kwargs):
        events.append('authorize')
        return initial

    def capability(*_args):
        events.append('capability')

    def complete(session, *_args, **kwargs):
        assert session is initial
        assert callable(kwargs['before_materialize'])
        events.append('complete')
        return item, status

    def get(*_args, **_kwargs):
        events.append('refetch')
        return reloaded

    def spec(session):
        assert session is reloaded
        events.append('target_spec')
        return SimpleNamespace(result_key=result_key)

    session = SimpleNamespace(commit=lambda: events.append('commit'),
                              rollback=lambda: events.append('rollback'), get=get)
    monkeypatch.setattr(uploads, 'db', SimpleNamespace(
        session=session, engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))))
    monkeypatch.setattr(uploads, 'current_app_user', lambda **_kwargs: object())
    monkeypatch.setattr(uploads, '_v2_direct_completion_session', creator)
    monkeypatch.setattr(uploads, '_v2_multipart_completion_session', creator)
    monkeypatch.setattr(uploads, 'require_direct_capability', capability)
    monkeypatch.setattr(uploads, 'require_capability', capability)
    monkeypatch.setattr(uploads, 'complete_direct_session', complete)
    monkeypatch.setattr(uploads, 'complete_session', complete)
    monkeypatch.setattr(uploads, '_v2_session_or_404', get)
    monkeypatch.setattr(multipart_upload_service, 'spec_for_session', spec)
    path = ('gallery/direct' if mode == 'gallery' else f'uploads/{mode}')
    return app.test_client(), f'/api/v2/bands/7/{path}/opaque-session/complete'


def _expected_events(mode, item_events):
    return ['authorize', 'capability', 'complete', 'commit', 'refetch'] + (
        ['session_kind'] if mode == 'direct' else
        ['target_spec'] if mode == 'multipart' else []
    ) + ['session_id'] + item_events


@pytest.mark.parametrize('mode,kind,result_key', CASES, ids=CASE_IDS)
@pytest.mark.parametrize('status', [201, 200], ids=['new', 'retry'])
@pytest.mark.parametrize('value', ['truthy', 'falsey', 'none'])
def test_completion_preserves_envelope_status_and_post_commit_order(
        monkeypatch, mode, kind, result_key, status, value):
    events = []
    item = None if value == 'none' else ResultItem(events, truth=value == 'truthy')
    client, path = _client(monkeypatch, events, mode, kind, result_key, item, status)

    response = client.post(path, headers={'X-Upload-Capability': 'test-only'})

    assert response.status_code == status
    expected_item = ({'id': 42, 'filename': 'original.mp4', 'transcoding_status': 'queued',
                      'url': None, 'audio_url': None, 'legacy_only': True}
                     if value == 'truthy' else None)
    assert response.get_json() == {
        'session_id': 'opaque-session', 'status': 'completed', result_key: expected_item,
    }
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.vary
    item_events = ([] if value == 'none' else ['item_truth'])
    if value == 'truthy':
        item_events.append('serialize')
    assert events == _expected_events(mode, item_events)


@pytest.mark.parametrize('mode,kind,result_key', CASES, ids=CASE_IDS)
def test_completion_serialization_failure_is_after_commit_without_rollback(
        monkeypatch, mode, kind, result_key):
    events = []
    item = ResultItem(events, fail=True)
    client, path = _client(monkeypatch, events, mode, kind, result_key, item, 201)

    with pytest.raises(RuntimeError, match='serialization failed after commit'):
        client.post(path)

    assert events == _expected_events(mode, ['item_truth', 'serialize'])
    assert 'rollback' not in events


def test_direct_completion_preserves_non_personal_kind_media_fallback(monkeypatch):
    events = []
    client, path = _client(monkeypatch, events, 'direct', 'other', 'media', None, 200)
    response = client.post(path)
    assert response.get_json() == {
        'session_id': 'opaque-session', 'status': 'completed', 'media': None,
    }
    assert events == _expected_events('direct', [])
