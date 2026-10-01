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


def test_promote_suggestion_requires_admin_password_and_preserves_the_candidate_on_failure(client):
    suggestion = _create_suggestion(client)
    response = client.post(f"/suggestions/{suggestion['id']}/promote", json={'password': 'wrong'})
    assert response.status_code == 400
    assert client.get('/suggestions').get_json()[0]['id'] == suggestion['id']
    assert client.get('/songs').get_json() == []


def test_promote_suggestion_creates_a_practice_song_and_removes_the_candidate(client):
    suggestion = _create_suggestion(client)
    response = client.post(f"/suggestions/{suggestion['id']}/promote", json={'password': 'admin'})
    assert response.status_code == 201
    song = response.get_json()['song']
    assert song['title'] == suggestion['title']
    assert song['artist'] == suggestion['artist']
    assert song['link'] == suggestion['link']
    assert song['memo'] == suggestion['memo']
    assert song['status'] == 'Practice'
    assert client.get('/suggestions').get_json() == []
    assert client.get('/songs').get_json()[0]['id'] == song['id']


@pytest.mark.parametrize('corrupt_link', (
    'https://example.test/%0Aheader', 'https://example.test/%250Aheader',
    'https://user:password@example.test/', 'https://example.test/%C2%85',
))
def test_promote_revalidates_legacy_corrupt_link_before_creating_or_deleting(client, app, corrupt_link):
    """Promotion must not turn a previously stored unsafe link into a song."""
    suggestion = _create_suggestion(client)
    with app.app_context():
        from extensions import db
        from models import SongSuggestion
        db.session.get(SongSuggestion, suggestion['id']).link = corrupt_link
        db.session.commit()

    response = client.post(f"/suggestions/{suggestion['id']}/promote", json={'password': 'admin'})
    assert response.status_code == 400
    assert client.get('/songs').get_json() == []
    assert [item['id'] for item in client.get('/suggestions').get_json()] == [suggestion['id']]
