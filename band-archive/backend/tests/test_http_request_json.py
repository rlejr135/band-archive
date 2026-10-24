"""Preserved bounded JSON contracts, without application or database setup."""
from io import BytesIO
from types import SimpleNamespace

import pytest
from flask import Flask, g, request

import auth_service
import http_request_json
from errors import ValidationError, register_error_handlers
from routes import availability, member_profiles


class RecordingStream:
    def __init__(self, body):
        self.body = BytesIO(body)
        self.read_sizes = []

    def read(self, size):
        self.read_sizes.append(size)
        return self.body.read(size)


def install_request(monkeypatch, body, content_length=None):
    stream = RecordingStream(body)
    monkeypatch.setattr(http_request_json, 'request', SimpleNamespace(
        content_length=content_length, stream=stream,
    ))
    return stream


def assert_error(status, message):
    with pytest.raises(ValidationError) as caught:
        http_request_json.payload()
    assert caught.value.status_code == status
    assert caught.value.message == message
    assert caught.value.code is None


def test_declared_oversize_is_rejected_before_reading(monkeypatch):
    stream = install_request(monkeypatch, b'{}', 32769)
    assert_error(413, 'Calendar request too large.')
    assert stream.read_sizes == []


@pytest.mark.parametrize('content_length', [None, 32768])
def test_exact_limit_object_is_accepted_with_one_bounded_read(monkeypatch, content_length):
    empty_object = b'{"value":""}'
    value = 'x' * (32768 - len(empty_object))
    body = b'{"value":"' + value.encode('ascii') + b'"}'
    assert len(body) == 32768
    stream = install_request(monkeypatch, body, content_length)
    assert http_request_json.payload() == {'value': value}
    assert stream.read_sizes == [32769]


@pytest.mark.parametrize('content_length', [None, 1])
def test_stream_oversize_is_rejected_even_without_accurate_length(monkeypatch, content_length):
    stream = install_request(monkeypatch, b'x' * 40000, content_length)
    assert_error(413, 'Calendar request too large.')
    assert stream.read_sizes == [32769]
    assert stream.body.tell() == 32769


@pytest.mark.parametrize('body', [
    b'', b'{', b'{"value":}', b'\xff',
    b'{"name":"first","name":"second"}',
    b'{"nested":{"name":"first","name":"second"}}',
    b'{"nested":[{"name":"first","name":"second"}]}',
])
def test_invalid_or_duplicate_json_keeps_exact_error(monkeypatch, body):
    install_request(monkeypatch, body)
    assert_error(400, 'A valid JSON object is required.')


@pytest.mark.parametrize('error_type', [ValueError, TypeError, UnicodeError, RecursionError])
def test_existing_decoder_exception_boundary_is_preserved(monkeypatch, error_type):
    install_request(monkeypatch, b'{}')

    def fail_decode(*args, **kwargs):
        raise error_type('synthetic decoder failure')

    monkeypatch.setattr(http_request_json.json, 'loads', fail_decode)
    assert_error(400, 'A valid JSON object is required.')


@pytest.mark.parametrize('body', [b'[]', b'null', b'false', b'1', b'"text"'])
def test_valid_nonobject_json_keeps_distinct_error(monkeypatch, body):
    install_request(monkeypatch, body)
    assert_error(400, 'A JSON object is required.')


def test_nested_object_and_empty_object_remain_supported(monkeypatch):
    install_request(monkeypatch, b'{"nested":{"name":"member"},"items":[1,null]}')
    assert http_request_json.payload() == {'nested': {'name': 'member'}, 'items': [1, None]}
    install_request(monkeypatch, b'{}')
    assert http_request_json.payload() == {}


@pytest.fixture
def endpoint_contract(monkeypatch):
    app = Flask('bounded-json-contract')
    app.config.update(TESTING=True, BAND_API_SCHEMA_READY=True)
    register_error_handlers(app)
    calls = []

    def authenticate():
        calls.append('authenticate')
        if request.headers.get('Authorization') != 'Bearer contract-test':
            raise auth_service.AuthError('missing_token', 'Authentication required.')
        g.current_app_user = SimpleNamespace(id=7)
        return g.current_app_user

    def update_days(actor, band_id, body):
        calls.append(('availability', actor.id, band_id, body))
        return {'changed': True, 'generation': 0}

    def own_profile(actor, band_id, body=None):
        calls.append(('profile', actor.id, band_id, body))
        return {'name': body['name'], 'profile_revision': body['profile_revision'] + 1}

    monkeypatch.setattr(auth_service, 'authenticate_request', authenticate)
    monkeypatch.setattr(availability.service, 'update_days', update_days)
    monkeypatch.setattr(member_profiles, 'own_profile', own_profile)
    app.register_blueprint(availability.availability_bp)
    app.register_blueprint(member_profiles.member_profiles_bp)
    return app, calls


ENDPOINTS = [
    ('PUT', '/api/v2/bands/3/availability/me/batch'),
    ('PATCH', '/api/v2/bands/3/members/me'),
]


@pytest.mark.parametrize('method,path', ENDPOINTS)
def test_schema_gate_precedes_authentication_and_parsing(endpoint_contract, method, path):
    app, calls = endpoint_contract
    app.config['BAND_API_SCHEMA_READY'] = False
    response = app.test_client().open(path, method=method, data=b'x' * 32769,
                                      content_type='application/json')
    assert response.status_code == 503
    assert response.get_json() == {'error': 'band_schema_unavailable'}
    assert calls == []
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.vary


@pytest.mark.parametrize('method,path', ENDPOINTS)
@pytest.mark.parametrize('body', [b'{', b'x' * 32769], ids=['malformed-json', 'oversized-body'])
def test_authentication_precedes_body_parsing(endpoint_contract, method, path, body):
    app, calls = endpoint_contract
    response = app.test_client().open(path, method=method, data=body,
                                      content_type='application/json')
    assert response.status_code == 401
    assert response.get_json() == {'error': 'missing_token', 'message': 'Authentication required.'}
    assert calls == ['authenticate']
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.vary


@pytest.mark.parametrize('method,path', ENDPOINTS)
@pytest.mark.parametrize('body,status,message', [
    (b'x' * 32769, 413, 'Calendar request too large.'),
    (b'{"nested":{"name":1,"name":2}}', 400, 'A valid JSON object is required.'),
    (b'[]', 400, 'A JSON object is required.'),
], ids=['oversized-body', 'duplicate-nested-key', 'nonobject-json'])
def test_authenticated_endpoint_preserves_parser_errors(endpoint_contract, method, path,
                                                       body, status, message):
    app, calls = endpoint_contract
    response = app.test_client().open(path, method=method, data=body,
        content_type='application/json', headers={'Authorization': 'Bearer contract-test'})
    assert response.status_code == status
    assert response.get_json() == {'error': message}
    assert calls == ['authenticate']
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.vary


@pytest.mark.parametrize('method,path,body,response_body,target', [
    ('PUT', '/api/v2/bands/3/availability/me/batch', {'generation': 0, 'days': []},
     {'changed': True, 'generation': 0}, 'availability'),
    ('PATCH', '/api/v2/bands/3/members/me', {'profile_revision': 2, 'name': 'Band-local name'},
     {'member': {'name': 'Band-local name', 'profile_revision': 3}}, 'profile'),
])
def test_valid_body_reaches_existing_service_once(endpoint_contract, method, path,
                                                body, response_body, target):
    app, calls = endpoint_contract
    response = app.test_client().open(path, method=method, json=body,
        headers={'Authorization': 'Bearer contract-test'})
    assert response.status_code == 200
    assert response.get_json() == response_body
    assert calls == ['authenticate', (target, 7, 3, body)]
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.vary


def test_both_routes_import_the_neutral_parser_directly():
    assert availability.payload is http_request_json.payload
    assert member_profiles.payload is http_request_json.payload
    assert http_request_json.payload.__module__ == 'http_request_json'
