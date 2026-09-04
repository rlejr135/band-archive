"""Contracts shared by media and personal-log resumable uploads."""

import pytest

from models import Media, PersonalLog
from storage import storage


def _song(client):
    return client.post('/songs', json={'title': 'Upload contract', 'artist': 'Band'}).get_json()['id']


def _member(client):
    return client.post('/members', json={'name': 'Upload member', 'instrument': 'Drums'}).get_json()['id']


def _initiate(client, kind, declared_bytes=32):
    payload = {
        'filename': 'contract.mp4',
        'content_type': 'video/mp4',
        'declared_bytes': declared_bytes,
    }
    if kind == 'media':
        payload['song_id'] = _song(client)
    else:
        payload.update(member_id=_member(client), title='Contract log')
    response = client.post('/uploads/multipart/initiate', json=payload)
    assert response.status_code == 201
    session = response.get_json()
    return session, {session['capability_header']: session['upload_capability_token']}


def _issue_and_ack(client, session_id, headers, declared_bytes):
    assert client.post(
        f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}, headers=headers,
    ).status_code == 200
    return client.post(
        f'/uploads/multipart/{session_id}/parts/1/ack',
        json={'etag': 'contract-etag', 'bytes': declared_bytes}, headers=headers,
    )


@pytest.mark.parametrize('kind', ('media', 'personal_log'))
def test_resumable_upload_capability_and_part_contract_is_identical(client, kind):
    session, headers = _initiate(client, kind)
    session_id = session['session_id']

    assert client.get(f'/uploads/multipart/{session_id}').status_code == 403
    first = client.post(f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}, headers=headers)
    assert first.status_code == 200
    assert client.post(
        f'/uploads/multipart/{session_id}/parts', json={'part_number': 1}, headers=headers,
    ).status_code == 200
    acknowledged = client.post(
        f'/uploads/multipart/{session_id}/parts/1/ack',
        json={'etag': 'contract-etag', 'bytes': 32}, headers=headers,
    )
    assert acknowledged.status_code == 200
    assert client.post(
        f'/uploads/multipart/{session_id}/parts/1/ack',
        json={'etag': 'contract-etag', 'bytes': 32}, headers=headers,
    ).status_code == 200
    assert client.post(
        f'/uploads/multipart/{session_id}/parts/1/ack',
        json={'etag': 'different', 'bytes': 32}, headers=headers,
    ).status_code == 409


@pytest.mark.parametrize('kind, model, response_key', (
    ('media', Media, 'media'),
    ('personal_log', PersonalLog, 'personal_log'),
))
def test_resumable_completion_recovers_from_r2_timeout_for_both_targets(
        client, app, monkeypatch, kind, model, response_key):
    session, headers = _initiate(client, kind)
    _issue_and_ack(client, session['session_id'], headers, 32)
    monkeypatch.setattr(
        storage, 'complete_multipart_upload',
        lambda *args: (_ for _ in ()).throw(TimeoutError('response lost after completion')),
    )
    monkeypatch.setattr(storage, 'head', lambda key: {'ContentLength': 32})

    completed = client.post(f"/uploads/multipart/{session['session_id']}/complete", headers=headers)
    assert completed.status_code == 201
    item_id = completed.get_json()[response_key]['id']
    repeated = client.post(f"/uploads/multipart/{session['session_id']}/complete", headers=headers)
    assert repeated.status_code == 200
    assert repeated.get_json()[response_key]['id'] == item_id
    with app.app_context():
        assert app.extensions['sqlalchemy'].session.get(model, item_id) is not None


@pytest.mark.parametrize('kind', ('media', 'personal_log'))
def test_resumable_abort_is_idempotent_for_both_targets(client, monkeypatch, kind):
    session, headers = _initiate(client, kind)
    aborted = []
    monkeypatch.setattr(storage, 'abort_multipart_upload', lambda key, upload_id: aborted.append((key, upload_id)))

    assert client.post(f"/uploads/multipart/{session['session_id']}/abort", headers=headers).status_code == 200
    assert client.post(f"/uploads/multipart/{session['session_id']}/abort", headers=headers).status_code == 200
    assert len(aborted) == 1
