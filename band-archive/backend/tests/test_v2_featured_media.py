"""One owner-selected, visibility-safe representative media item per song."""

import pytest

from extensions import db
from sqlalchemy import text
from models import AppUser, Band, BandMembership, ContentRestriction, Media, R2DeletionTask, Rehearsal, Song
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_v2_gallery_featured_write import _token_for
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401


@pytest.fixture
def featured_graph(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        band.default_visibility = 'PUBLIC'
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        song = Song(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                    title='Featured song', artist='Example artist')
        db.session.add(song)
        db.session.flush()
        audio = Media(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      song_id=song.id, filename='audio.mp3', object_key='bands/a/media/audio.mp3',
                      file_type='audio', vote_score=9)
        video = Media(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      song_id=song.id, filename='video.mp4', object_key='bands/a/media/video.mp4',
                      file_type='video', vote_score=-3)
        private = Media(band_id=band.id, created_by_membership_id=owner.id, visibility='MEMBERS',
                        song_id=song.id, filename='private.mp3', object_key='bands/a/media/private.mp3',
                        file_type='audio')
        db.session.add_all((audio, video, private))
        db.session.commit()
        return {'band': band.id, 'song': song.id, 'audio': audio.id, 'video': video.id,
                'private': private.id}


@pytest.fixture
def featured_principals(signer):
    return {
        name: _token_for(signer, subject, email)
        for name, subject, email in (
            ('owner', '11111111-1111-1111-1111-111111111111', 'user@example.com'),
            ('admin', '22222222-2222-2222-2222-222222222222', 'admin@example.com'),
            ('member', '33333333-3333-3333-3333-333333333333', 'member@example.com'),
            ('outsider', '77777777-7777-7777-7777-777777777777', 'outsider@example.com'),
        )
    }


def _song_url(graph):
    return f"/api/v2/bands/{graph['band']}/songs/{graph['song']}/featured-media"


def test_featured_media_permissions_include_only_owner_or_current_eligible_superadmin(
        access_app, featured_graph, featured_principals, signer):
    client = access_app.test_client()
    path = _song_url(featured_graph)
    assert client.put(path, json={'media_id': featured_graph['audio']}).status_code == 401
    for role in ('admin', 'member'):
        assert client.put(path, headers=featured_principals[role],
                          json={'media_id': featured_graph['audio']}).status_code == 403

    with access_app.app_context():
        outsider = AppUser.query.filter_by(email='outsider@example.com').one()
        outsider.is_platform_admin = True
        db.session.get(Band, featured_graph['band']).default_visibility = 'MEMBERS'
        db.session.commit()
    assert client.put(path, headers=featured_principals['outsider'],
                      json={'media_id': featured_graph['audio']}).status_code == 200

    with access_app.app_context():
        outsider = AppUser.query.filter_by(email='outsider@example.com').one()
        outsider.status = 'inactive'
        db.session.commit()
    denied = client.delete(path, headers=featured_principals['outsider'])
    assert denied.status_code == 403

    with access_app.app_context():
        outsider = AppUser.query.filter_by(email='outsider@example.com').one()
        outsider.status = 'active'
        outsider.email_verified = False
        db.session.commit()
    unverified_token = {'Authorization': 'Bearer ' + _token(
        signer, sub='77777777-7777-7777-7777-777777777777',
        email='outsider@example.com', email_verified=False,
    )}
    unverified = client.put(path, headers=unverified_token,
                            json={'media_id': featured_graph['audio']})
    assert unverified.status_code == 401
    unverified_owner_token = {'Authorization': 'Bearer ' + _token(
        signer, sub='11111111-1111-1111-1111-111111111111',
        email='user@example.com', email_verified=False,
    )}
    assert client.delete(path, headers=unverified_owner_token).status_code == 401

    with access_app.app_context():
        owner_membership = BandMembership.query.filter_by(
            band_id=featured_graph['band'], role='owner', status='active',
        ).one()
        owner_membership.status = 'left'
        db.session.commit()
    former_owner = client.delete(path, headers=featured_principals['owner'])
    assert former_owner.status_code == 403


def test_featured_media_can_switch_audio_video_clear_without_touching_media_or_votes(
        access_app, featured_graph, featured_principals):
    client = access_app.test_client()
    path = _song_url(featured_graph)
    first = client.put(path, headers=featured_principals['owner'], json={'media_id': featured_graph['audio']})
    assert first.status_code == 200
    assert first.get_json() == {
        'song_id': featured_graph['song'], 'media_id': featured_graph['audio'],
        'is_featured': True, 'can_feature': True,
    }
    switched = client.put(path, headers=featured_principals['owner'], json={'media_id': featured_graph['video']})
    assert switched.status_code == 200
    with access_app.app_context():
        assert Media.query.filter_by(song_id=featured_graph['song'], is_featured=True).all() == [
            db.session.get(Media, featured_graph['video']),
        ]
        assert db.session.get(Media, featured_graph['audio']).vote_score == 9
        assert db.session.get(Media, featured_graph['video']).vote_score == -3
        total_media = Media.query.filter_by(song_id=featured_graph['song']).count()
    cleared = client.delete(path, headers=featured_principals['owner'])
    assert cleared.status_code == 200 and cleared.get_json()['cleared'] is True
    repeated = client.delete(path, headers=featured_principals['owner'])
    assert repeated.status_code == 200 and repeated.get_json()['cleared'] is False
    with access_app.app_context():
        assert Media.query.filter_by(song_id=featured_graph['song'], is_featured=True).count() == 0
        assert Media.query.filter_by(song_id=featured_graph['song']).count() == total_media


def test_featured_media_rejects_cross_song_private_invalid_and_malformed_candidates(
        access_app, featured_graph, featured_principals):
    client = access_app.test_client()
    path = _song_url(featured_graph)
    for body in (None, {}, {'media_id': '1'}, {'media_id': True}, {'media_id': featured_graph['audio'], 'extra': 1}):
        assert client.put(path, headers=featured_principals['owner'], json=body).status_code == 400
    assert client.put(path, headers=featured_principals['owner'], json={'media_id': 99999}).status_code == 404
    with access_app.app_context():
        media = db.session.get(Media, featured_graph['private'])
        media.original_pruned_at = media.created_at
        db.session.commit()
    assert client.put(path, headers=featured_principals['owner'],
                      json={'media_id': featured_graph['private']}).status_code == 404

    with access_app.app_context():
        band = db.session.get(Band, featured_graph['band'])
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        other_song = Song(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                          title='Other song', artist='Example artist')
        db.session.add(other_song)
        db.session.flush()
        other_media = Media(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                            song_id=other_song.id, filename='other.mp3', object_key='bands/a/media/other.mp3',
                            file_type='audio')
        db.session.add(other_media)
        db.session.commit()
        other_media_id = other_media.id
        other_song_id = other_song.id
    assert client.put(path, headers=featured_principals['owner'],
                      json={'media_id': other_media_id}).status_code == 404
    assert client.put(path, headers=featured_principals['owner'],
                      json={'media_id': featured_graph['audio']}).status_code == 200
    # Clearing another song in the same band is valid; it must not clear the
    # representative on the original song.
    assert client.delete(_song_url({**featured_graph, 'song': other_song_id}),
                         headers=featured_principals['owner']).status_code == 200
    with access_app.app_context():
        assert Media.query.filter_by(id=featured_graph['audio'], is_featured=True).count() == 1

    with access_app.app_context():
        band_b = Band.query.filter_by(slug='band-b').one()
        owner_b = BandMembership.query.filter_by(band_id=band_b.id, role='owner').one()
        foreign_song = Song(band_id=band_b.id, created_by_membership_id=owner_b.id, visibility='PUBLIC',
                            title='Foreign song', artist='Other artist')
        db.session.add(foreign_song)
        db.session.flush()
        foreign_media = Media(band_id=band_b.id, created_by_membership_id=owner_b.id, visibility='PUBLIC',
                              song_id=foreign_song.id, filename='foreign.mp3',
                              object_key='bands/b/media/foreign.mp3', file_type='audio')
        db.session.add(foreign_media)
        db.session.commit()
        foreign_song_id = foreign_song.id
        foreign_media_id = foreign_media.id
    assert client.put(path, headers=featured_principals['owner'],
                      json={'media_id': foreign_media_id}).status_code == 404
    assert client.delete(_song_url({**featured_graph, 'song': foreign_song_id}),
                         headers=featured_principals['owner']).status_code == 404
    candidates = f"/api/v2/bands/{featured_graph['band']}/songs/{foreign_song_id}/featured-media/candidates"
    assert client.get(candidates, headers=featured_principals['owner']).status_code == 404


def test_featured_media_read_scope_capabilities_and_management_candidates(
        access_app, featured_graph, featured_principals, monkeypatch):
    from storage import storage

    signed = []
    monkeypatch.setattr(storage, 'generate_url',
                        lambda key, expires_in=None: signed.append((key, expires_in)) or f'url:{key}')
    client = access_app.test_client()
    songs_url = f"/api/v2/bands/{featured_graph['band']}/songs"
    anonymous = client.get(songs_url).get_json()['songs'][0]
    assert anonymous['can_feature'] is False
    owner = client.get(songs_url, headers=featured_principals['owner']).get_json()['songs'][0]
    assert owner['can_feature'] is True

    with access_app.app_context():
        outsider = AppUser.query.filter_by(email='outsider@example.com').one()
        outsider.is_platform_admin = True
        db.session.get(Band, featured_graph['band']).default_visibility = 'MEMBERS'
        db.session.commit()
    candidate_url = f"/api/v2/bands/{featured_graph['band']}/songs/{featured_graph['song']}/featured-media/candidates"
    candidates = client.get(candidate_url, headers=featured_principals['outsider'])
    assert candidates.status_code == 200
    candidate_data = candidates.get_json()['media']
    assert {item['id'] for item in candidate_data} == {
        featured_graph['audio'], featured_graph['video'], featured_graph['private'],
    }
    assert all('playback' not in item and 'url' not in item for item in candidate_data)
    assert signed == []

    # The narrow management route bypasses the private-band read boundary for
    # this eligible nonmember admin, while the ordinary representative list
    # remains private.
    assert client.get(f"/api/v2/bands/{featured_graph['band']}/featured-media").status_code == 404

    chosen = client.put(_song_url(featured_graph), headers=featured_principals['owner'],
                        json={'media_id': featured_graph['audio']})
    public_list = client.get(f"/api/v2/bands/{featured_graph['band']}/featured-media",
                             headers=featured_principals['member'])
    assert public_list.status_code == 200
    item = public_list.get_json()['media'][0]
    assert item['song_summary'] == {
        'id': featured_graph['song'], 'title': 'Featured song', 'artist': 'Example artist',
    }
    assert item['playback']['url'] == f"url:bands/a/media/audio.mp3"
    assert chosen.status_code == 200
    assert client.put(_song_url(featured_graph), headers=featured_principals['owner'],
                      json={'media_id': featured_graph['private']}).status_code == 200
    hidden = client.get(f"/api/v2/bands/{featured_graph['band']}/featured-media")
    assert hidden.status_code == 404
    visible_to_member = client.get(
        f"/api/v2/bands/{featured_graph['band']}/featured-media",
        headers=featured_principals['member'],
    )
    assert visible_to_member.status_code == 200
    assert [entry['id'] for entry in visible_to_member.get_json()['media']] == [featured_graph['private']]


def test_representative_management_catalog_is_narrow_private_and_paginated(
        access_app, featured_graph, featured_principals):
    with access_app.app_context():
        band = db.session.get(Band, featured_graph['band'])
        band.default_visibility = 'MEMBERS'
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        restricted_song = Song(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                               title='Restricted catalog song', artist='Hidden')
        invalid_song = Song(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                            title='Invalid catalog song', artist='Hidden')
        db.session.add_all((restricted_song, invalid_song))
        db.session.flush()
        owner_user = owner.user
        db.session.add(ContentRestriction(
            band_id=band.id, resource_type='song', resource_id=restricted_song.id,
            case_ref='af3910b2-41cd-4777-8fae-86cd9819bb45', reason_code='privacy',
            created_by_user_id=owner_user.id,
        ))
        invalid_id = invalid_song.id
        db.session.commit()
        db.session.execute(text('PRAGMA ignore_check_constraints=ON'))
        db.session.execute(text("UPDATE song SET visibility='INVALID' WHERE id=:id"), {'id': invalid_id})
        db.session.commit()
        db.session.execute(text('PRAGMA ignore_check_constraints=OFF'))
        outsider = AppUser.query.filter_by(email='outsider@example.com').one()
        outsider.is_platform_admin = True
        db.session.commit()

    client = access_app.test_client()
    url = '/api/v2/bands/by-slug/band-a/manage/representative-media'
    assert client.get(url).status_code == 401
    assert client.get('/api/v2/bands/by-slug/missing/manage/representative-media',
                      headers=featured_principals['outsider']).status_code == 404
    assert client.get(url, headers=featured_principals['admin']).status_code == 403

    response = client.get(url, headers=featured_principals['outsider'], query_string={'limit': 1})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['band'] == {
        'id': featured_graph['band'], 'slug': 'band-a', 'name': 'Band A', 'can_feature': True,
    }
    assert payload['pagination']['limit'] == 1
    assert payload['pagination']['total'] == 4
    assert payload['pagination']['has_more'] is True
    assert len(payload['songs']) == 1
    assert payload['songs'][0]['can_feature'] is True
    assert all(set(song) == {'id', 'title', 'artist', 'can_feature'} for song in payload['songs'])

    # This route doesn't turn the private band's general discovery/content
    # routes into a superadmin data-access shortcut.
    assert client.get('/api/v2/bands/by-slug/band-a',
                      headers=featured_principals['outsider']).status_code == 404
    assert client.get(f"/api/v2/bands/{featured_graph['band']}/songs",
                      headers=featured_principals['outsider']).status_code == 404

    candidates_url = (
        f"/api/v2/bands/{featured_graph['band']}/songs/{featured_graph['song']}"
        '/featured-media/candidates'
    )
    candidates = client.get(candidates_url, headers=featured_principals['outsider'])
    assert candidates.status_code == 200
    assert any(item['id'] == featured_graph['private'] for item in candidates.get_json()['media'])
    set_response = client.put(_song_url(featured_graph), headers=featured_principals['outsider'],
                              json={'media_id': featured_graph['audio']})
    assert set_response.status_code == 200
    clear_response = client.delete(_song_url(featured_graph), headers=featured_principals['outsider'])
    assert clear_response.status_code == 200

    owner_response = client.get(url, headers=featured_principals['owner'], query_string={'limit': 100})
    assert owner_response.status_code == 200
    assert owner_response.get_json()['pagination']['total'] == 4


def test_legacy_featured_media_write_is_disabled(access_app, featured_graph, featured_principals):
    response = access_app.test_client().patch(
        f"/media/{featured_graph['audio']}/featured", headers=featured_principals['owner'],
    )
    assert response.status_code in (404, 410)


def test_featured_candidates_filter_restricted_parent_and_pending_media_before_pagination(
        access_app, featured_graph, featured_principals):
    from datetime import date

    with access_app.app_context():
        band = db.session.get(Band, featured_graph['band'])
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        song = db.session.get(Song, featured_graph['song'])
        rehearsal = Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                              title='Linked rehearsal', date=date(2026, 10, 1))
        db.session.add(rehearsal)
        db.session.flush()
        song.rehearsals.append(rehearsal)
        db.session.get(Media, featured_graph['audio']).rehearsal_id = rehearsal.id
        db.session.commit()
        rehearsal_id = rehearsal.id

    client = access_app.test_client()
    url = f"/api/v2/bands/{featured_graph['band']}/songs/{featured_graph['song']}/featured-media/candidates"
    all_candidates = client.get(url, headers=featured_principals['owner'], query_string={'limit': 1})
    assert all_candidates.status_code == 200
    assert all_candidates.get_json()['pagination']['total'] == 3

    with access_app.app_context():
        db.session.execute(text('PRAGMA ignore_check_constraints=ON'))
        db.session.execute(text("UPDATE rehearsal SET visibility='INVALID' WHERE id=:id"), {'id': rehearsal_id})
        db.session.commit()
        db.session.execute(text('PRAGMA ignore_check_constraints=OFF'))
    invalid_parent = client.get(url, headers=featured_principals['owner'], query_string={'limit': 1})
    assert invalid_parent.status_code == 200
    assert invalid_parent.get_json()['pagination']['total'] == 0
    assert featured_graph['audio'] not in {item['id'] for item in invalid_parent.get_json()['media']}

    with access_app.app_context():
        db.session.execute(text('PRAGMA ignore_check_constraints=ON'))
        db.session.execute(text("UPDATE rehearsal SET visibility='PUBLIC' WHERE id=:id"), {'id': rehearsal_id})
        db.session.commit()
        db.session.execute(text('PRAGMA ignore_check_constraints=OFF'))

    with access_app.app_context():
        owner_user = BandMembership.query.filter_by(band_id=featured_graph['band'], role='owner').one().user
        db.session.add(ContentRestriction(
            band_id=featured_graph['band'], resource_type='rehearsal', resource_id=rehearsal_id,
            case_ref='f7f8d93e-7c82-4e88-9851-7ac260ac9a03', reason_code='privacy',
            created_by_user_id=owner_user.id,
        ))
        db.session.commit()
    restricted = client.get(url, headers=featured_principals['owner'], query_string={'limit': 1})
    assert restricted.status_code == 200
    assert restricted.get_json()['pagination']['total'] == 0

    with access_app.app_context():
        item = ContentRestriction.query.filter_by(resource_id=rehearsal_id, resource_type='rehearsal').one()
        item.state = 'released'
        from datetime import datetime, timezone
        item.released_at = datetime.now(timezone.utc)
        db.session.commit()

    with access_app.app_context():
        owner_user = BandMembership.query.filter_by(band_id=featured_graph['band'], role='owner').one().user
        db.session.add(R2DeletionTask(
            band_id=featured_graph['band'], resource_type='media', resource_id=featured_graph['video'],
            status='queued', object_keys=['bands/a/media/video.mp4'], actor_user_id=owner_user.id,
        ))
        db.session.commit()
    pending = client.get(url, headers=featured_principals['owner'], query_string={'limit': 1})
    assert pending.status_code == 200
    assert pending.get_json()['pagination']['total'] == 2
    assert featured_graph['video'] not in {item['id'] for item in pending.get_json()['media']}


def test_featured_candidates_exclude_pending_direct_and_linked_rehearsal_parents(
        access_app, featured_graph, featured_principals):
    from datetime import date

    with access_app.app_context():
        band = db.session.get(Band, featured_graph['band'])
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        song = db.session.get(Song, featured_graph['song'])
        direct = Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                           title='Direct', date=date(2026, 10, 1))
        linked = Rehearsal(band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
                           title='Linked', date=date(2026, 10, 2))
        db.session.add_all((direct, linked))
        db.session.flush()
        song.rehearsals.append(linked)
        db.session.get(Media, featured_graph['video']).rehearsal_id = direct.id
        db.session.commit()
        owner_user = owner.user
        direct_id, linked_id = direct.id, linked.id

    client = access_app.test_client()
    url = f"/api/v2/bands/{featured_graph['band']}/songs/{featured_graph['song']}/featured-media/candidates"
    normal = client.get(url, headers=featured_principals['owner'], query_string={'limit': 1})
    assert normal.status_code == 200
    assert normal.get_json()['pagination']['total'] == 3

    with access_app.app_context():
        db.session.execute(text('PRAGMA ignore_check_constraints=ON'))
        db.session.add(R2DeletionTask(
            band_id=featured_graph['band'], resource_type='rehearsal', resource_id=direct_id,
            status='queued', object_keys=['bands/a/rehearsals/direct.mp4'], actor_user_id=owner_user.id,
        ))
        db.session.commit()
        db.session.execute(text('PRAGMA ignore_check_constraints=OFF'))
    direct_pending = client.get(url, headers=featured_principals['owner'], query_string={'limit': 1})
    assert direct_pending.status_code == 200
    assert direct_pending.get_json()['pagination']['total'] == 2
    assert featured_graph['video'] not in {item['id'] for item in direct_pending.get_json()['media']}

    with access_app.app_context():
        db.session.execute(text('PRAGMA ignore_check_constraints=ON'))
        db.session.add(R2DeletionTask(
            band_id=featured_graph['band'], resource_type='rehearsal', resource_id=linked_id,
            status='queued', object_keys=['bands/a/rehearsals/linked.mp4'], actor_user_id=owner_user.id,
        ))
        db.session.commit()
        db.session.execute(text('PRAGMA ignore_check_constraints=OFF'))
    linked_pending = client.get(url, headers=featured_principals['owner'], query_string={'limit': 1})
    assert linked_pending.status_code == 200
    assert linked_pending.get_json()['pagination']['total'] == 0
    assert featured_graph['audio'] not in {item['id'] for item in linked_pending.get_json()['media']}
