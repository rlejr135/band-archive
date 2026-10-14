import json

import pytest
import requests

from place_search import PlaceSearchGuard
from extensions import db
from models import AppUser, Band, BandMembership
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import access_app, signer


@pytest.fixture
def search_access(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        bands = {band.slug: band.id for band in Band.query.all()}
        users = {user.email: user.id for user in AppUser.query.all()}
        db.session.add(BandMembership(
            band_id=bands['band-b'], user_id=users['member@example.com'],
            role='member', status='active',
        ))
        db.session.commit()
    client = access_app.test_client()

    def token(email):
        subject = {
            'user@example.com': '11111111-1111-1111-1111-111111111111',
            'admin@example.com': '22222222-2222-2222-2222-222222222222',
            'member@example.com': '33333333-3333-3333-3333-333333333333',
            'pending@example.com': '44444444-4444-4444-4444-444444444444',
            'rejected@example.com': '55555555-5555-5555-5555-555555555555',
            'left@example.com': '66666666-6666-6666-6666-666666666666',
            'outsider@example.com': '77777777-7777-7777-7777-777777777777',
        }[email]
        return _token(signer, sub=subject, email=email)

    def get(email=None, band='band-a', query='studio', **kwargs):
        headers = kwargs.pop('headers', {})
        if email:
            headers = {**headers, 'Authorization': f'Bearer {token(email)}'}
        return client.get(
            f"/api/v2/bands/{bands.get(band, band)}/search-places?query={query}",
            headers=headers, **kwargs,
        )

    return {'app': access_app, 'client': client, 'bands': bands, 'users': users,
            'token': token, 'get': get}


def _payload(count=1):
    return {
        'items': [
            {
                'title': '<b>Studio</b> &amp; Practice',
                'address': 'Address',
                'roadAddress': 'Road address',
                'mapx': '1270012345',
                'mapy': '0375012345',
            }
            for _ in range(count)
        ],
    }


class ProviderResponse:
    def __init__(self, payload=None, *, chunks=None, status_error=None, headers=None, status_code=200):
        body = json.dumps(payload).encode('utf-8') if payload is not None else b''
        self.chunks = chunks if chunks is not None else [body]
        self.status_error = status_error
        self.headers = headers or {}
        self.status_code = status_code
        self.closed = False

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error
        if self.status_code >= 400:
            raise requests.HTTPError('provider failure')

    def iter_content(self, chunk_size):
        assert chunk_size == 8192
        return iter(self.chunks)

    def close(self):
        self.closed = True


def _set_dummy_provider_credentials(monkeypatch):
    monkeypatch.setenv('NAVER_SEARCH_CLIENT_ID', 'test-client-id')
    monkeypatch.setenv('NAVER_SEARCH_CLIENT_SECRET', 'test-client-secret')


def _configure_guard(app, **options):
    defaults = {
        'request_limit': 10,
        'upstream_limit': 60,
        'window_seconds': 60,
        'cache_ttl_seconds': 30,
        'cache_max_entries': 512,
        'max_subjects': 4096,
        'max_concurrent_upstream': 2,
    }
    defaults.update(options)
    app.extensions['place_search_guard'] = PlaceSearchGuard(**defaults)
    return app.extensions['place_search_guard']


def test_empty_and_invalid_queries_do_not_call_provider(search_access, monkeypatch):
    calls = []
    monkeypatch.setattr('routes.search.requests.get', lambda *args, **kwargs: calls.append(args))

    get = search_access['get']
    assert get('member@example.com', query='%20%20').get_json() == []
    assert get('member@example.com', query='a%00b').status_code == 400
    assert get('member@example.com', query='x' * 101).status_code == 400
    assert calls == []


def test_place_search_success_is_bounded_cached_and_preserves_list_contract(search_access, monkeypatch):
    _set_dummy_provider_credentials(monkeypatch)
    calls = []
    response = ProviderResponse(_payload(count=8))

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr('routes.search.requests.get', fake_get)
    first = search_access['get']('member@example.com', query='Studio%20%20Practice')
    second = search_access['get']('member@example.com', query='studio%20practice')

    assert first.status_code == 200
    result = first.get_json()
    assert len(result) == 5
    assert result[0] == {
        'title': 'Studio & Practice',
        'address': 'Address',
        'roadAddress': 'Road address',
        'mapx': '1270012345',
        'mapy': '0375012345',
    }
    assert second.get_json() == result
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == 'https://openapi.naver.com/v1/search/local.json'
    assert kwargs['params'] == {'query': 'Studio Practice', 'display': 5}
    assert kwargs['timeout'] == (2, 3)
    assert kwargs['stream'] is True
    assert kwargs['allow_redirects'] is False
    assert response.closed


def test_membership_precedes_validation_cache_and_provider(search_access, monkeypatch):
    calls = []
    _set_dummy_provider_credentials(monkeypatch)
    monkeypatch.setattr('routes.search.requests.get', lambda *a, **kw: calls.append(a) or ProviderResponse(_payload()))
    get = search_access['get']
    assert get().status_code == 401
    assert get('user@example.com', band='band-b').status_code == 404
    assert get('pending@example.com').status_code == 404
    assert get('rejected@example.com').status_code == 404
    assert get('left@example.com').status_code == 404
    assert get('outsider@example.com', band='not-a-band').status_code == 404
    with search_access['app'].app_context():
        user = AppUser.query.filter_by(email='outsider@example.com').one()
        user.is_platform_admin = True
        disabled = AppUser.query.filter_by(email='user@example.com').one()
        disabled.status = 'disabled'
        db.session.commit()
    assert get('outsider@example.com').status_code == 404
    assert get('user@example.com').status_code == 403
    assert calls == []


def test_cached_search_still_requires_current_active_membership(search_access, monkeypatch):
    _set_dummy_provider_credentials(monkeypatch)
    calls = []
    monkeypatch.setattr('routes.search.requests.get', lambda *a, **kw: calls.append(a) or ProviderResponse(_payload()))
    get = search_access['get']
    assert get('member@example.com').status_code == 200
    with search_access['app'].app_context():
        membership = BandMembership.query.filter_by(
            band_id=search_access['bands']['band-a'],
            user_id=search_access['users']['member@example.com'],
        ).one()
        membership.status = 'left'
        db.session.commit()
    assert get('member@example.com').status_code == 404
    assert len(calls) == 1


def test_legacy_public_route_is_gone_even_with_valid_jwt_and_compat_flag(search_access, monkeypatch):
    calls = []
    monkeypatch.setattr('routes.search.requests.get', lambda *a, **kw: calls.append(a))
    search_access['app'].config['LEGACY_WRITES_ENABLED'] = True
    headers = {'Authorization': f"Bearer {search_access['token']('member@example.com')}"}
    response = search_access['client'].get('/api/search-places?query=studio', headers=headers)
    assert response.status_code == 410
    assert response.get_json() == {'error': 'place_search_requires_band_membership'}
    assert calls == []


def test_personal_request_limit_shared_across_bands_and_ignores_identity_headers(search_access, monkeypatch):
    _set_dummy_provider_credentials(monkeypatch)
    _configure_guard(search_access['app'], request_limit=1)
    monkeypatch.setattr(
        'routes.search.requests.get', lambda *args, **kwargs: ProviderResponse(_payload()),
    )

    get = search_access['get']
    first = get('member@example.com', query='first', environ_overrides={'REMOTE_ADDR': '192.0.2.1'},
                headers={'X-Forwarded-For': '203.0.113.77', 'X-User-ID': '999'})
    second = get('member@example.com', band='band-b', query='second',
                 environ_overrides={'REMOTE_ADDR': '192.0.2.2'},
                 headers={'X-Forwarded-For': '198.51.100.99', 'X-User-ID': '123'})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json() == {'error': 'rate_limited'}
    assert int(second.headers['Retry-After']) >= 1


def test_different_users_share_upstream_budget_but_have_personal_buckets(search_access, monkeypatch):
    _set_dummy_provider_credentials(monkeypatch)
    _configure_guard(search_access['app'], request_limit=1, upstream_limit=1)
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return ProviderResponse(_payload())

    monkeypatch.setattr('routes.search.requests.get', fake_get)

    get = search_access['get']
    first = get('member@example.com', query='first', environ_overrides={'REMOTE_ADDR': '192.0.2.1'})
    # Same socket, different authenticated user: separate personal quota; cache is global.
    cache_hit = get('admin@example.com', query='first', environ_overrides={'REMOTE_ADDR': '192.0.2.1'})
    second = get('admin@example.com', query='second', environ_overrides={'REMOTE_ADDR': '192.0.2.1'})

    assert first.status_code == 200
    assert cache_hit.status_code == 200
    assert second.status_code == 429
    assert len(calls) == 1
    assert int(second.headers['Retry-After']) >= 1


@pytest.mark.parametrize('provider_response', [
    ProviderResponse(chunks=[b'not-json']),
    ProviderResponse(payload=[]),
    ProviderResponse(payload={'items': {}}),
    ProviderResponse(status_error=requests.HTTPError('provider response body must not leak')),
    ProviderResponse(payload=_payload(), status_code=302),
    ProviderResponse(chunks=[b'x' * (256 * 1024 + 1)]),
    ProviderResponse(headers={'Content-Length': str(256 * 1024 + 1)}),
])
def test_provider_errors_return_generic_safe_response_and_release_slot(
    search_access, monkeypatch, provider_response,
):
    _set_dummy_provider_credentials(monkeypatch)
    guard = _configure_guard(search_access['app'], max_concurrent_upstream=1)
    monkeypatch.setattr(
        'routes.search.requests.get', lambda *args, **kwargs: provider_response,
    )

    response = search_access['get']('member@example.com', query='bounded')

    assert response.status_code == 502
    assert response.get_json() == {'error': 'place_search_upstream_unavailable'}
    assert b'provider response body must not leak' not in response.data
    assert provider_response.closed
    reserved, _ = guard.reserve_upstream()
    assert reserved
    guard.release_upstream()


def test_network_failure_returns_generic_safe_response(search_access, monkeypatch):
    _set_dummy_provider_credentials(monkeypatch)

    def fail(*args, **kwargs):
        raise requests.Timeout('sensitive provider details')

    monkeypatch.setattr('routes.search.requests.get', fail)
    response = search_access['get']('member@example.com', query='timeout')

    assert response.status_code == 502
    assert response.get_json() == {'error': 'place_search_upstream_unavailable'}
    assert b'sensitive provider details' not in response.data


def test_missing_provider_credentials_return_generic_unavailable(search_access, monkeypatch):
    monkeypatch.delenv('NAVER_SEARCH_CLIENT_ID', raising=False)
    monkeypatch.delenv('NAVER_SEARCH_CLIENT_SECRET', raising=False)
    calls = []
    monkeypatch.setattr('routes.search.requests.get', lambda *args, **kwargs: calls.append(args))

    response = search_access['get']('member@example.com', query='missing')

    assert response.status_code == 503
    assert response.get_json() == {'error': 'place_search_unavailable'}
    assert calls == []


def test_place_search_guard_expires_and_bounds_cache_and_limiter_state():
    now = [10.0]
    guard = PlaceSearchGuard(
        cache_ttl_seconds=5, cache_max_entries=2, max_subjects=2,
        request_limit=1, window_seconds=3, clock=lambda: now[0],
    )
    guard.cache_results('one', [{'title': '1'}])
    guard.cache_results('two', [{'title': '2'}])
    guard.cache_results('three', [{'title': '3'}])
    assert len(guard._cache) == 2
    assert guard.get_cached('one') == (False, None)
    assert guard.get_cached('two')[0] is True

    assert guard.allow_request('192.0.2.1') == (True, 0)
    assert guard.allow_request('192.0.2.1')[0] is False
    assert guard.allow_request('192.0.2.2') == (True, 0)
    assert guard.allow_request('192.0.2.3')[0] is False
    assert len(guard._requests_by_subject) == 2
    now[0] += 4
    assert guard.allow_request('192.0.2.3')[0] is True  # expired subjects are pruned

    now[0] += 5
    assert guard.get_cached('two') == (False, None)


def test_concurrent_upstream_slot_fails_fast_and_releases():
    guard = PlaceSearchGuard(max_concurrent_upstream=1)
    assert guard.reserve_upstream() == (True, 0)
    accepted, retry_after = guard.reserve_upstream()
    assert accepted is False
    assert retry_after >= 1
    guard.release_upstream()
    assert guard.reserve_upstream() == (True, 0)
    guard.release_upstream()


def test_provider_result_field_lengths_and_coordinates_are_validated():
    from place_search import sanitize_place_results

    results = sanitize_place_results({'items': [{
        'title': '<b>' + ('t' * 250) + '</b>',
        'address': 'a' * 500,
        'roadAddress': 'r' * 500,
        'mapx': '12x',
        'mapy': '3' * 13,
    }]})

    assert len(results) == 1
    assert len(results[0]['title']) == 200
    assert len(results[0]['address']) == 300
    assert len(results[0]['roadAddress']) == 300
    assert results[0]['mapx'] == ''
    assert results[0]['mapy'] == ''

    unicode_coords = sanitize_place_results({'items': [{
        'mapx': '１２３４５',
        'mapy': '²²',
    }]})[0]
    assert unicode_coords['mapx'] == ''
    assert unicode_coords['mapy'] == ''
