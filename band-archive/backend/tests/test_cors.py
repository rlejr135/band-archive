from app import create_app
from config import TestingConfig


ALLOWED_ORIGINS = (
    'https://rlejr135.github.io',
    'https://band-archive.pages.dev',
)


def _production_app(tmp_path, monkeypatch, origins=None):
    if origins is None:
        origins = ','.join(ALLOWED_ORIGINS)
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', origins)

    class BrowserConfig(TestingConfig):
        DEBUG = False
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'cors.db'}"

    return create_app(BrowserConfig, start_worker=False)


def _preflight(client, path, origin, method='PATCH', headers='content-type,x-upload-capability,x-voter-id'):
    return client.options(path, headers={
        'Origin': origin,
        'Access-Control-Request-Method': method,
        'Access-Control-Request-Headers': headers,
    })


def test_production_cors_preflight_allows_frontends_and_required_headers(tmp_path, monkeypatch):
    app = _production_app(tmp_path, monkeypatch)
    for origin in ALLOWED_ORIGINS:
        response = _preflight(
            app.test_client(), '/media/123/vote', origin,
            headers='authorization,content-type,x-upload-capability,x-voter-id',
        )
        assert response.status_code == 200
        assert response.headers['Access-Control-Allow-Origin'] == origin
        assert response.headers['Access-Control-Allow-Credentials'] == 'true'
        allowed_headers = {
            header.strip().lower()
            for header in response.headers['Access-Control-Allow-Headers'].split(',')
        }
        assert {
            'authorization', 'content-type', 'x-upload-capability', 'x-voter-id',
        } <= allowed_headers
        allowed_methods = {
            method.strip().upper()
            for method in response.headers['Access-Control-Allow-Methods'].split(',')
        }
        assert {'GET', 'POST', 'PATCH', 'OPTIONS'} <= allowed_methods


def test_production_cors_rejects_unlisted_origin(tmp_path, monkeypatch):
    app = _production_app(tmp_path, monkeypatch)
    response = _preflight(
        app.test_client(), '/api/v2/bands/1/songs', 'https://attacker.example',
    )
    assert 'Access-Control-Allow-Origin' not in response.headers
    assert 'Access-Control-Allow-Credentials' not in response.headers


def test_production_cors_denies_private_network_preflight_by_default(tmp_path, monkeypatch):
    app = _production_app(tmp_path, monkeypatch)
    response = app.test_client().options('/api/v2/bands/1/songs', headers={
        'Origin': ALLOWED_ORIGINS[0],
        'Access-Control-Request-Method': 'GET',
        'Access-Control-Request-Private-Network': 'true',
    })
    assert response.headers['Access-Control-Allow-Origin'] == ALLOWED_ORIGINS[0]
    assert response.headers.get('Access-Control-Allow-Private-Network', '').lower() != 'true'


def test_production_origin_allowlist_is_uniform_for_encoded_and_plus_paths(tmp_path, monkeypatch):
    app = _production_app(tmp_path, monkeypatch)
    client = app.test_client()
    for path in (
        '/api/v2/bands/1/songs',
        '/media/a+b/vote',
        '/media/a%2Bb/vote',
        '/media/a%2fb/vote',
    ):
        allowed = _preflight(client, path, ALLOWED_ORIGINS[0])
        denied = _preflight(client, path, 'https://attacker.example')
        assert allowed.headers.get('Access-Control-Allow-Origin') == ALLOWED_ORIGINS[0], path
        assert 'Access-Control-Allow-Origin' not in denied.headers, path


def test_production_cors_rejects_method_outside_explicit_contract(tmp_path, monkeypatch):
    app = _production_app(tmp_path, monkeypatch)
    response = _preflight(
        app.test_client(), '/api/v2/bands/1/songs', ALLOWED_ORIGINS[0], method='TRACE',
    )
    allowed_methods = response.headers.get('Access-Control-Allow-Methods', '')
    assert 'TRACE' not in {method.strip().upper() for method in allowed_methods.split(',')}


def test_production_cors_rejects_headers_outside_explicit_contract(tmp_path, monkeypatch):
    app = _production_app(tmp_path, monkeypatch)
    response = _preflight(
        app.test_client(), '/api/v2/bands/1/songs', ALLOWED_ORIGINS[0], headers='x-untrusted-header',
    )
    allowed_headers = response.headers.get('Access-Control-Allow-Headers', '')
    assert 'x-untrusted-header' not in {
        header.strip().lower() for header in allowed_headers.split(',')
    }


def test_debug_cors_keeps_local_development_origin_usable(tmp_path, monkeypatch):
    monkeypatch.delenv('CORS_ALLOWED_ORIGINS', raising=False)

    class LocalConfig(TestingConfig):
        DEBUG = True
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'debug-cors.db'}"

    app = create_app(LocalConfig, start_worker=False)
    response = app.test_client().get('/health', headers={
        'Origin': 'http://localhost:5173',
    })
    assert response.headers['Access-Control-Allow-Origin'] == 'http://localhost:5173'
    assert response.headers['Access-Control-Allow-Credentials'] == 'true'
