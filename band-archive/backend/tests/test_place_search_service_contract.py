"""Ordering contracts for the member-authorized place-search coordinator.

HTTP authentication is covered by test_search_places; these tests use an
already verified local user identity and never contact the provider.
"""

import pytest
import requests
from flask import Flask

from place_search import PlaceSearchGuard
from routes import search


class RecordingGuard:
    def __init__(self, events, *, allowed=True, cached=False, reserved=True):
        self.events = events
        self.allowed = allowed
        self.cached = cached
        self.reserved = reserved

    def normalize_query(self, raw):
        self.events.append(('normalize', raw))
        return PlaceSearchGuard.normalize_query(raw)

    def allow_request(self, identity):
        self.events.append(('personal', identity))
        return self.allowed, 17

    def get_cached(self, query):
        self.events.append(('cache_lookup', query))
        return self.cached, [{'title': 'cached'}]

    def reserve_upstream(self):
        self.events.append('reserve')
        return self.reserved, 23

    def release_upstream(self):
        self.events.append('release')

    def cache_results(self, query, results):
        self.events.append(('cache_write', query, results))


@pytest.fixture
def coordinator(monkeypatch):
    app = Flask(__name__)
    events = []
    monkeypatch.setenv('NAVER_SEARCH_CLIENT_ID', 'synthetic-client')
    monkeypatch.setenv('NAVER_SEARCH_CLIENT_SECRET', 'synthetic-secret')

    def invoke(*, query=' Studio  Practice ', guard_options=None):
        app.extensions['place_search_guard'] = RecordingGuard(events, **(guard_options or {}))
        with app.test_request_context(query_string={'query': query}):
            response = app.make_response(search.search_places_for_member(42))
        return response, events

    invoke.events = events
    return invoke


@pytest.mark.parametrize(('query', 'options', 'status', 'payload', 'expected', 'retry'), [
    ('a\x00b', {}, 400, {'error': 'invalid_query'}, [('normalize', 'a\x00b')], None),
    ('  ', {}, 200, [], [('normalize', '  ')], None),
    ('studio', {'allowed': False}, 429, {'error': 'rate_limited'},
     [('normalize', 'studio'), ('personal', 'user:42')], '17'),
    ('studio', {'cached': True}, 200, [{'title': 'cached'}],
     [('normalize', 'studio'), ('personal', 'user:42'), ('cache_lookup', 'studio')], None),
    ('studio', {'reserved': False}, 429, {'error': 'rate_limited'},
     [('normalize', 'studio'), ('personal', 'user:42'), ('cache_lookup', 'studio'), 'reserve'], '23'),
])
def test_short_circuit_order(coordinator, monkeypatch, query, options, status, payload, expected, retry):
    def unexpected_provider(*args, **kwargs):
        pytest.fail('short-circuited search must not contact provider')

    monkeypatch.setattr(search.requests, 'get', unexpected_provider)
    response, events = coordinator(query=query, guard_options=options)
    assert response.status_code == status
    assert response.get_json() == payload
    assert response.headers.get('Retry-After') == retry
    assert events == expected


def test_cache_hit_precedes_missing_credentials(coordinator, monkeypatch):
    monkeypatch.delenv('NAVER_SEARCH_CLIENT_ID')
    monkeypatch.delenv('NAVER_SEARCH_CLIENT_SECRET')
    cached, events = coordinator(query='studio', guard_options={'cached': True})
    assert cached.status_code == 200
    assert cached.get_json() == [{'title': 'cached'}]
    assert events == [('normalize', 'studio'), ('personal', 'user:42'), ('cache_lookup', 'studio')]
    events.clear()
    missing, events = coordinator(query='studio')
    assert missing.status_code == 503
    assert missing.get_json() == {'error': 'place_search_unavailable'}
    assert events == [('normalize', 'studio'), ('personal', 'user:42'), ('cache_lookup', 'studio')]


@pytest.mark.parametrize('close_raises', [False, True])
def test_stream_is_closed_and_slot_released_before_success_is_cached(coordinator, monkeypatch, close_raises):
    events = coordinator.events

    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            events.append('status')

        def iter_content(self, chunk_size):
            assert chunk_size == 8192
            events.append('stream')
            yield b'{"items": []}'

        def close(self):
            events.append('close')
            if close_raises:
                raise RuntimeError('private close failure')

    def provider(*args, **kwargs):
        events.append('provider')
        return Response()

    monkeypatch.setattr(search.requests, 'get', provider)
    response, _ = coordinator()
    assert response.status_code == 200
    assert response.get_json() == []
    assert events == [
        ('normalize', ' Studio  Practice '), ('personal', 'user:42'),
        ('cache_lookup', 'Studio Practice'), 'reserve', 'provider', 'status',
        'stream', 'close', 'release', ('cache_write', 'Studio Practice', []),
    ]


@pytest.mark.parametrize('failure', ['network', 'deadline_empty_chunk', 'invalid_length', 'invalid_utf8'])
def test_failed_provider_attempt_releases_exactly_once_and_never_caches(coordinator, monkeypatch, failure):
    events = coordinator.events

    class Response:
        status_code = 200
        headers = {'Content-Length': 'invalid'} if failure == 'invalid_length' else {}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b'' if failure == 'deadline_empty_chunk' else b'\xff'

        def close(self):
            events.append('close')
            raise RuntimeError('private close failure')

    def provider(*args, **kwargs):
        events.append('provider')
        if failure == 'network':
            raise requests.Timeout('private network failure')
        return Response()

    monkeypatch.setattr(search.requests, 'get', provider)
    if failure == 'deadline_empty_chunk':
        ticks = iter([100, 105])
        monkeypatch.setattr(search.time, 'monotonic', lambda: next(ticks))
    response, _ = coordinator(query='studio')
    assert response.status_code == 502
    assert response.get_json() == {'error': 'place_search_upstream_unavailable'}
    expected_tail = ['provider', 'release'] if failure == 'network' else ['provider', 'close', 'release']
    assert events == [
        ('normalize', 'studio'), ('personal', 'user:42'), ('cache_lookup', 'studio'),
        'reserve', *expected_tail,
    ]
