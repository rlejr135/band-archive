"""Private-content and pending-deletion fences on surviving v1 readers."""
from datetime import date

import pytest

from extensions import db
from models import AppUser, Band, BandMembership, GalleryImage, Media, PersonalLog, Rehearsal, Song
from storage import storage
from tests.test_deletion_retry_contracts import (
    authenticated_client, deletion_app, signer, no_unmocked_bounded_delete,
    enqueue, headers, seed_file,
)


MEMBER_SUB = '33333333-3333-3333-3333-333333333333'


def legacy_file(app, kind):
    band_id, resource_id = seed_file(app, kind)
    model = {'media': Media, 'personal_log': PersonalLog, 'gallery': GalleryImage}[kind]
    with app.app_context():
        band = db.session.get(Band, band_id)
        band.default_visibility = 'PUBLIC'
        band.is_legacy_import = True
        target = db.session.get(model, resource_id)
        prefix = {'media': 'media', 'personal_log': 'personal_logs', 'gallery': 'gallery'}[kind]
        target.object_key = f'{prefix}/{target.filename}'
        db.session.commit()
        return band_id, resource_id, target.filename


@pytest.mark.parametrize('kind', ('media', 'personal_log'))
def test_legacy_source_get_stops_signing_as_soon_as_deletion_is_pending(
        deletion_app, signer, authenticated_client, monkeypatch, kind):
    band_id, resource_id, filename = legacy_file(deletion_app, kind)
    path = f'/uploads/{filename}' if kind == 'media' else f'/uploads/personal_logs/{filename}'
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: calls.append(key) or 'https://storage.test/object')
    assert authenticated_client.get(path).status_code == 302
    assert len(calls) == 1
    enqueue(authenticated_client, signer, band_id, kind, resource_id)
    calls.clear()
    response = authenticated_client.get(path)
    assert response.status_code == 404
    assert calls == []


@pytest.mark.parametrize('viewer', ('anonymous', 'left'))
@pytest.mark.parametrize('route', ('song_list', 'song_detail', 'song_media', 'processing', 'source'))
def test_song_made_members_only_via_v2_cannot_be_read_or_signed_through_v1(
        deletion_app, signer, authenticated_client, monkeypatch, viewer, route):
    band_id, resource_id, filename = legacy_file(deletion_app, 'media')
    with deletion_app.app_context():
        target = db.session.get(Media, resource_id)
        song_id = target.song_id
    changed = authenticated_client.patch(
        f'/api/v2/bands/{band_id}/songs/{song_id}', headers=headers(signer),
        json={'visibility': 'MEMBERS'})
    assert changed.status_code == 200
    auth = {}
    if viewer == 'left':
        with deletion_app.app_context():
            user = AppUser.query.filter_by(supabase_user_id=MEMBER_SUB).one()
            BandMembership.query.filter_by(band_id=band_id, user_id=user.id).one().status = 'left'
            db.session.commit()
        auth = headers(signer, MEMBER_SUB, 'member@example.com')
    paths = {'song_list': '/songs', 'song_detail': f'/songs/{song_id}',
             'song_media': f'/songs/{song_id}/media', 'processing': f'/media/{resource_id}/processing',
             'source': f'/uploads/{filename}'}
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: calls.append(key) or 'https://storage.test/private')
    response = authenticated_client.get(paths[route], headers=auth)
    assert calls == [], f'Private parent was bypassed by {paths[route]} ({response.status_code})'
    assert response.status_code in (200, 401, 403, 404, 410)
    if response.status_code == 200:
        # A legacy list may preserve public items, but not the private song.
        assert route == 'song_list'
        assert all(item['id'] != song_id for item in response.get_json())


@pytest.mark.parametrize('viewer', ('anonymous', 'left'))
@pytest.mark.parametrize('route', ('logs', 'processing', 'source'))
def test_member_only_personal_log_cannot_be_read_or_signed_through_v1(
        deletion_app, signer, authenticated_client, monkeypatch, viewer, route):
    band_id, resource_id, filename = legacy_file(deletion_app, 'personal_log')
    with deletion_app.app_context():
        target = db.session.get(PersonalLog, resource_id)
        target.visibility = 'MEMBERS'
        member_id = target.member_id
        if viewer == 'left':
            user = AppUser.query.filter_by(supabase_user_id=MEMBER_SUB).one()
            BandMembership.query.filter_by(band_id=band_id, user_id=user.id).one().status = 'left'
        db.session.commit()
    auth = {} if viewer == 'anonymous' else headers(signer, MEMBER_SUB, 'member@example.com')
    paths = {'logs': f'/members/{member_id}/logs', 'processing': f'/personal-logs/{resource_id}/processing',
             'source': f'/uploads/personal_logs/{filename}'}
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: calls.append(key) or 'https://storage.test/private')
    response = authenticated_client.get(paths[route], headers=auth)
    assert calls == [], f'Private log was bypassed by {paths[route]} ({response.status_code})'
    assert response.status_code in (200, 401, 403, 404, 410)
    if response.status_code == 200:
        assert route == 'logs'
        assert all(item['id'] != resource_id for item in response.get_json())


@pytest.mark.parametrize('route', ('/gallery', '/gallery/featured'))
def test_member_only_gallery_never_gets_anonymous_legacy_signed_url(
        deletion_app, signer, authenticated_client, monkeypatch, route):
    _band_id, resource_id, _filename = legacy_file(deletion_app, 'gallery')
    with deletion_app.app_context():
        target = db.session.get(GalleryImage, resource_id)
        target.visibility = 'MEMBERS'
        target.is_featured = True
        db.session.commit()
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: calls.append(key) or 'https://storage.test/private')
    response = authenticated_client.get(route)
    assert calls == [], f'Private gallery was bypassed by {route} ({response.status_code})'
    assert response.status_code in (200, 401, 403, 404, 410)
    if response.status_code == 200:
        payload = response.get_json()
        assert payload in ([], None) or isinstance(payload, dict) and payload.get('image') is None


@pytest.mark.parametrize('kind', ('media', 'personal_log', 'gallery'))
@pytest.mark.parametrize('route_kind', ('list', 'source_or_featured'))
def test_members_only_band_overrides_public_children_in_all_legacy_file_readers(
        deletion_app, signer, authenticated_client, monkeypatch, kind, route_kind):
    band_id, resource_id, filename = legacy_file(deletion_app, kind)
    with deletion_app.app_context():
        db.session.get(Band, band_id).default_visibility = 'MEMBERS'
        member_id = db.session.get(PersonalLog, resource_id).member_id if kind == 'personal_log' else None
        if kind == 'gallery':
            db.session.get(GalleryImage, resource_id).is_featured = True
        db.session.commit()
    paths = {'media': ('/songs', f'/uploads/{filename}'),
             'personal_log': (f'/members/{member_id}/logs', f'/uploads/personal_logs/{filename}'),
             'gallery': ('/gallery', '/gallery/featured')}
    path = paths[kind][0 if route_kind == 'list' else 1]
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: calls.append(key) or 'https://storage.test/private')
    response = authenticated_client.get(path)
    assert calls == [], f'Private band was bypassed by {path} ({response.status_code})'
    assert response.status_code in (200, 401, 403, 404, 410)
    if response.status_code == 200:
        assert response.get_json() in (None, [])


@pytest.mark.parametrize('route', ('song_list', 'song_detail', 'song_media'))
def test_public_song_stays_readable_but_private_child_media_is_omitted_before_signing(
        deletion_app, signer, authenticated_client, monkeypatch, route):
    _band_id, resource_id, _filename = legacy_file(deletion_app, 'media')
    with deletion_app.app_context():
        media = db.session.get(Media, resource_id)
        media.visibility = 'MEMBERS'
        song_id = media.song_id
        db.session.commit()
    paths = {'song_list': '/songs', 'song_detail': f'/songs/{song_id}', 'song_media': f'/songs/{song_id}/media'}
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: calls.append(key) or 'https://storage.test/private')
    response = authenticated_client.get(paths[route])
    assert response.status_code == 200, response.get_json()
    assert calls == []
    payload = response.get_json()
    if route == 'song_media':
        assert payload == []
    else:
        song = next(item for item in payload if item['id'] == song_id) if route == 'song_list' else payload
        assert song['id'] == song_id
        assert song['media'] == []


@pytest.mark.parametrize('route', ('song_list', 'song_detail', 'song_media', 'rehearsal_list',
                                 'rehearsal_detail', 'processing', 'source'))
def test_private_rehearsal_hides_its_public_media_but_keeps_public_song_readable(
        deletion_app, signer, authenticated_client, monkeypatch, route):
    band_id, resource_id, filename = legacy_file(deletion_app, 'media')
    with deletion_app.app_context():
        media = db.session.get(Media, resource_id)
        song = db.session.get(Song, media.song_id)
        rehearsal = Rehearsal(band_id=band_id, created_by_membership_id=media.created_by_membership_id,
                              visibility='MEMBERS', title='Synthetic private rehearsal', date=date(2026, 10, 17))
        rehearsal.songs.append(song)
        db.session.add(rehearsal)
        db.session.flush()
        media.rehearsal_id = rehearsal.id
        db.session.commit()
        rehearsal_id, song_id = rehearsal.id, song.id
    paths = {'song_list': '/songs', 'song_detail': f'/songs/{song_id}', 'song_media': f'/songs/{song_id}/media',
             'rehearsal_list': '/rehearsals', 'rehearsal_detail': f'/rehearsals/{rehearsal_id}',
             'processing': f'/media/{resource_id}/processing', 'source': f'/uploads/{filename}'}
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: calls.append(key) or 'https://storage.test/private')
    response = authenticated_client.get(paths[route])
    assert calls == [], f'Private rehearsal was bypassed by {paths[route]} ({response.status_code})'
    payload = response.get_json()
    if route in {'song_list', 'song_detail', 'song_media', 'rehearsal_list'}:
        assert response.status_code == 200, payload
        if route in {'song_media', 'rehearsal_list'}:
            assert payload == []
        else:
            song = next(item for item in payload if item['id'] == song_id) if route == 'song_list' else payload
            assert song['id'] == song_id and song['media'] == [] and song['rehearsals'] == []
    else:
        assert response.status_code in (401, 403, 404, 410)


@pytest.mark.parametrize('route', ('list', 'detail', 'logs'))
def test_private_roster_hides_member_and_nested_log_listing_without_signing(
        deletion_app, signer, authenticated_client, monkeypatch, route):
    band_id, resource_id, _filename = legacy_file(deletion_app, 'personal_log')
    with deletion_app.app_context():
        db.session.get(Band, band_id).member_roster_public = False
        member_id = db.session.get(PersonalLog, resource_id).member_id
        db.session.commit()
    paths = {'list': '/members', 'detail': f'/members/{member_id}', 'logs': f'/members/{member_id}/logs'}
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: calls.append(key) or 'https://storage.test/private')
    response = authenticated_client.get(paths[route])
    assert calls == []
    if response.status_code == 200:
        assert route == 'list' and response.get_json() == []
    else:
        assert response.status_code in (401, 403, 404, 410)


def test_public_member_log_list_omits_private_child_instead_of_rejecting_public_parent(
        deletion_app, signer, authenticated_client, monkeypatch):
    _band_id, resource_id, _filename = legacy_file(deletion_app, 'personal_log')
    with deletion_app.app_context():
        target = db.session.get(PersonalLog, resource_id)
        target.visibility = 'MEMBERS'
        member_id = target.member_id
        db.session.commit()
    calls = []
    monkeypatch.setattr(storage, 'generate_url', lambda key, **_options: calls.append(key) or 'https://storage.test/private')
    response = authenticated_client.get(f'/members/{member_id}/logs')
    assert response.status_code == 200
    assert response.get_json() == []
    assert calls == []
