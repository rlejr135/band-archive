import pytest


def _create_suggestion(client):
    response = client.post('/suggestions', json={
        'title': '새로운 곡',
        'artist': '새 아티스트',
        'link': 'https://youtu.be/example',
        'memo': '합주 후보',
    })
    assert response.status_code == 201
    return response.get_json()


def test_legacy_promote_suggestion_is_retired_and_preserves_the_candidate(client):
    suggestion = _create_suggestion(client)
    response = client.post(f"/suggestions/{suggestion['id']}/promote", json={'password': 'admin'})
    assert response.status_code == 410
    assert client.get('/suggestions').get_json()[0]['id'] == suggestion['id']
    assert client.get('/songs').get_json() == []


def test_legacy_promote_suggestion_remains_closed_even_with_legacy_password(client):
    suggestion = _create_suggestion(client)
    response = client.post(f"/suggestions/{suggestion['id']}/promote", json={'password': 'admin'})
    assert response.status_code == 410
    assert client.get('/suggestions').get_json()[0]['id'] == suggestion['id']
    assert client.get('/songs').get_json() == []


@pytest.mark.parametrize('corrupt_link', (
    'https://example.test/%0Aheader', 'https://example.test/%250Aheader',
    'https://user:password@example.test/', 'https://example.test/%C2%85',
))
def test_legacy_promote_remains_closed_for_existing_corrupt_candidates(client, app, corrupt_link):
    """Closed legacy promotion cannot remove or transform a source suggestion."""
    suggestion = _create_suggestion(client)
    with app.app_context():
        from extensions import db
        from models import SongSuggestion
        db.session.get(SongSuggestion, suggestion['id']).link = corrupt_link
        db.session.commit()

    response = client.post(f"/suggestions/{suggestion['id']}/promote", json={'password': 'admin'})
    assert response.status_code == 410
    assert client.get('/songs').get_json() == []
    assert [item['id'] for item in client.get('/suggestions').get_json()] == [suggestion['id']]
