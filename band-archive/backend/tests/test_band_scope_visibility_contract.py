"""Band policy propagation contracts before the explicit scope-field refactor.

Existing read tests cover private children, anonymous invitation previews and
legacy denial. These cases isolate the opposite edge: PUBLIC children still
inherit a MEMBERS band, including management and an authenticated preview.
"""

import pytest

from extensions import db
from models import Band, BandMembership, GalleryImage
from storage import storage
from tests.test_auth import _token
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_invite_links import (  # noqa: F401
    _auth, _isolated_process_limits, invite_auth, invite_signer,
)
from tests.test_v2_content_reads import content_graph  # noqa: F401


@pytest.mark.parametrize(('band_visibility', 'ttl'), [('PUBLIC', 3600), ('MEMBERS', 300)])
def test_public_media_inherits_band_policy_for_member_http_playback(
        access_app, content_graph, signer, monkeypatch, band_visibility, ttl):
    with access_app.app_context():
        db.session.get(Band, content_graph['public_band']).default_visibility = band_visibility
        db.session.commit()
    calls = []
    monkeypatch.setattr(storage, 'generate_url',
                        lambda key, expires_in=None: calls.append((key, expires_in)) or f'url:{key}')
    headers = {'Authorization': 'Bearer ' + _token(
        signer, sub='77777777-7777-7777-7777-777777777777', email='outsider@example.com',
    )}
    path = (f"/api/v2/bands/{content_graph['public_band']}/songs/"
            f"{content_graph['playback_song']}/media")
    client = access_app.test_client()
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    media = response.get_json()['media']
    assert len(media) == 1 and media[0]['id'] == content_graph['public_media']
    assert media[0]['visibility'] == 'PUBLIC'
    assert media[0]['effective_visibility'] == band_visibility
    assert calls == [('bands/b/public/visible-720.mp4', ttl),
                     ('bands/b/public/visible-audio.m4a', ttl)]
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response.headers['Vary']

    calls.clear()
    visitor = client.get(path)
    assert visitor.status_code == (200 if band_visibility == 'PUBLIC' else 404)
    assert calls == ([('bands/b/public/visible-720.mp4', 3600),
                      ('bands/b/public/visible-audio.m4a', 3600)]
                     if band_visibility == 'PUBLIC' else [])


def test_logo_management_public_image_in_private_band_uses_member_ttl(
        access_app, content_graph, signer, monkeypatch):
    with access_app.app_context():
        band = db.session.get(Band, content_graph['private_band'])
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        image = GalleryImage(band_id=band.id, created_by_membership_id=owner.id,
                             visibility='PUBLIC', filename='scope-logo.jpg',
                             object_key='bands/a/scope-logo.jpg')
        db.session.add(image)
        db.session.commit()
        image_id = image.id
    calls = []
    monkeypatch.setattr(storage, 'generate_url',
                        lambda key, expires_in=None: calls.append((key, expires_in)) or f'url:{key}')
    client = access_app.test_client()
    path = '/api/v2/bands/by-slug/band-a/logo-management'
    response = client.get(path, headers={'Authorization': f'Bearer {_token(signer)}'})
    assert response.status_code == 200
    assert response.get_json()['gallery'] == [
        {'id': image_id, 'url': 'url:bands/a/scope-logo.jpg', 'is_featured': False},
    ]
    assert calls == [('bands/a/scope-logo.jpg', 300)]
    calls.clear()
    assert client.get(path).status_code == 404
    assert calls == []


def test_private_invitation_preview_remains_public_only_even_for_active_owner(
        invite_auth, monkeypatch):
    app, signer = invite_auth
    client = app.test_client()
    headers = _auth(signer)
    with app.app_context():
        band_id = Band.query.filter_by(slug='band-a').one().id
    created = client.post(f'/api/v2/bands/{band_id}/invite-links', json={}, headers=headers)
    assert created.status_code == 201
    calls = []
    monkeypatch.setattr(storage, 'generate_url',
                        lambda key, expires_in=None: calls.append((key, expires_in)) or f'url:{key}')
    response = client.post('/api/v2/invite-links/preview',
                           json={'token': created.get_json()['token']}, headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['viewer'] == {'membership_status': 'active', 'can_open_workspace': True}
    introduction = payload['invite']
    assert introduction['song_count_scope'] == 'PUBLIC'
    assert introduction['stats']['total_songs'] == 0
    assert introduction['featured_gallery'] is None
    assert introduction['announcement'] is None
    assert calls == []


def test_legacy_read_scope_keeps_band_policy_snapshot_without_reassigning_scope(
        access_app, content_graph, signer):
    from legacy_read_visibility import legacy_read_scope
    from content_visibility import song_visibility
    from models import Song

    with access_app.test_request_context(headers={'Authorization': f'Bearer {_token(signer)}'}):
        scope = legacy_read_scope(content_graph['private_band'])
        song = db.session.get(Song, content_graph['foreign_song'])
        assert song.visibility == 'PUBLIC'
        assert scope.band_visibility == song_visibility(scope, song) == 'MEMBERS'
        # A scope is a request-local policy snapshot. The legacy caller does
        # not assign the old attach helper's return; caching must retain the
        # resolved policy, not silently fall back to PUBLIC on a later call.
        db.session.get(Band, scope.band_id).default_visibility = 'PUBLIC'
        assert legacy_read_scope(scope.band_id) is scope
        assert song_visibility(scope, song) == 'MEMBERS'
