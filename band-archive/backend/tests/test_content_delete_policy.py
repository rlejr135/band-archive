"""Owner/superadmin-only deletion policy and legacy bypass regressions."""

from datetime import date

import pytest
from sqlalchemy import text

from extensions import db
from models import (Announcement, AppUser, Band, BandMembership, Comment,
                    Media, Rehearsal, Song, SongSuggestion)
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import access_app, signer


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


def _principals(signer):
    return {
        'owner': _headers(_token(signer)),
        'admin': _headers(_token(signer, sub='22222222-2222-2222-2222-222222222222',
                                 email='admin@example.com')),
        'member': _headers(_token(signer, sub='33333333-3333-3333-3333-333333333333',
                                  email='member@example.com')),
    }


@pytest.fixture
def delete_client(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    return access_app.test_client()


def _band_id(access_app):
    with access_app.app_context():
        return Band.query.filter_by(slug='band-a').one().id


def test_delete_requires_verified_owner_or_in_band_platform_admin(access_app, delete_client, signer):
    band_id = _band_id(access_app)
    principals = _principals(signer)
    with access_app.app_context():
        band = db.session.get(Band, band_id)
        member = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        song = Song(band_id=band_id, created_by_membership_id=member.id,
                    visibility='PUBLIC', title='member song', artist='artist')
        db.session.add(song)
        db.session.commit()
        song_id = song.id

    base = f'/api/v2/bands/{band_id}'
    with access_app.app_context():
        public_song_id = Song.query.filter_by(title='B public').one().id
    assert delete_client.delete(f'{base}/songs/{public_song_id}', headers=principals['owner']).status_code == 404
    # Authorship does not grant delete, but the existing author-edit contract remains.
    assert delete_client.patch(f'{base}/songs/{song_id}', headers=principals['member'],
                               json={'title': 'edited by author'}).status_code == 200
    assert delete_client.delete(f'{base}/songs/{song_id}', headers=principals['member']).status_code == 403
    assert delete_client.delete(f'{base}/songs/{song_id}', headers=principals['admin']).status_code == 403

    # A DB-granted, active, verified platform admin may delete only through this
    # in-band route; a JWT claim alone is never used to set this flag.
    with access_app.app_context():
        member_user = AppUser.query.filter_by(email='member@example.com').one()
        member_user.is_platform_admin = True
        member_user.status = 'active'
        member_user.email_verified = True
        db.session.commit()
    assert delete_client.delete(f'{base}/songs/{song_id}', headers=principals['member']).status_code == 200

    # Normal owners still delete; active band admins retain edit authority but
    # do not gain delete authority.
    with access_app.app_context():
        owner = BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').one()
        second = Song(band_id=band_id, created_by_membership_id=owner.id,
                      visibility='PUBLIC', title='owner song', artist='artist')
        db.session.add(second)
        db.session.commit()
        second_id = second.id
    assert delete_client.delete(f'{base}/songs/{second_id}', headers=principals['owner']).status_code == 200


def test_unverified_platform_token_cannot_delete(access_app, delete_client, signer):
    band_id = _band_id(access_app)
    with access_app.app_context():
        member = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        actor = db.session.get(AppUser, member.user_id)
        actor.is_platform_admin = True
        member_user_id = member.user_id
        song = Song(band_id=band_id, created_by_membership_id=member.id,
                    visibility='PUBLIC', title='verified gate', artist='artist')
        db.session.add(song)
        db.session.commit()
        song_id = song.id
    unverified = _headers(_token(signer, sub='33333333-3333-3333-3333-333333333333',
                                 email='member@example.com', email_verified=False))
    response = delete_client.delete(f'/api/v2/bands/{band_id}/songs/{song_id}', headers=unverified)
    assert response.status_code in (401, 403)
    disabled = _headers(_token(signer, sub='33333333-3333-3333-3333-333333333333',
                               email='member@example.com'))
    with access_app.app_context():
        db.session.get(AppUser, member_user_id).status = 'suspended'
        db.session.commit()
    assert delete_client.delete(f'/api/v2/bands/{band_id}/songs/{song_id}', headers=disabled).status_code == 403
    with access_app.app_context():
        assert db.session.get(Song, song_id) is not None


def test_database_unverified_platform_admin_cannot_delete(access_app, delete_client, signer, monkeypatch):
    band_id = _band_id(access_app)
    with access_app.app_context():
        member = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        actor = db.session.get(AppUser, member.user_id)
        actor.is_platform_admin = True
        actor.email_verified = False
        song = Song(band_id=band_id, created_by_membership_id=member.id,
                    visibility='PUBLIC', title='db unverified gate', artist='artist')
        db.session.add(song)
        db.session.commit()
        song_id = song.id

    # Auth normally synchronizes verified identity claims before this policy;
    # stub only that boundary to exercise a currently persisted unverified DB row.
    import auth_service
    from flask import g

    def authenticate_without_sync():
        user = AppUser.query.filter_by(email='member@example.com').one()
        g.current_app_user = user
        return user

    monkeypatch.setattr(auth_service, 'authenticate_request', authenticate_without_sync)
    headers = _headers(_token(signer, sub='33333333-3333-3333-3333-333333333333',
                              email='member@example.com'))
    response = delete_client.delete(f'/api/v2/bands/{band_id}/songs/{song_id}', headers=headers)
    assert response.status_code == 403
    with access_app.app_context():
        assert db.session.get(Song, song_id) is not None


def test_platform_admin_must_be_in_band_and_current_db_grant_is_rechecked(
        access_app, delete_client, signer, monkeypatch):
    band_id = _band_id(access_app)
    principals = _principals(signer)
    with access_app.app_context():
        admin_membership = BandMembership.query.filter_by(band_id=band_id, role='admin', status='active').one()
        nonmember_user = AppUser.query.filter_by(email='pending@example.com').one()
        nonmember_user.status = 'active'
        nonmember_user.email_verified = True
        nonmember_user.is_platform_admin = True
        song = Song(band_id=band_id, created_by_membership_id=admin_membership.id,
                    visibility='PUBLIC', title='protected', artist='artist')
        db.session.add(song)
        db.session.commit()
        song_id = song.id
        nonmember_token = _headers(_token(signer, sub='44444444-4444-4444-4444-444444444444',
                                          email='pending@example.com'))

    base = f'/api/v2/bands/{band_id}'
    # The generic authenticated write path cannot turn a nonmember platform
    # administrator into an in-band writer.
    assert delete_client.delete(f'{base}/songs/{song_id}', headers=nonmember_token).status_code == 403
    assert delete_client.get('/api/v2/bands/by-slug/band-a', headers=nonmember_token).status_code == 404

    # Simulate auth holding a stale platform-admin snapshot after the DB grant
    # was revoked. The destructive route must reload the actor from the DB.
    with access_app.app_context():
        member_user = AppUser.query.filter_by(email='member@example.com').one()
        member_user.email_verified = True
        member_user.is_platform_admin = False
        db.session.commit()
    stale_actor = {'id': None}
    import routes.content_writes as writes
    original_current_app_user = writes.current_app_user

    def current_user_with_revocation(*, required=False):
        user = original_current_app_user(required=required)
        stale_actor['id'] = user.id
        # Return a stale auth snapshot without dirtying the ORM identity map;
        # the route's populate_existing query should observe the DB's false.
        from types import SimpleNamespace
        return SimpleNamespace(
            id=user.id, supabase_user_id=user.supabase_user_id,
            status=user.status, email_verified=user.email_verified,
            is_platform_admin=True, display_name=user.display_name, email=user.email,
        )

    monkeypatch.setattr(writes, 'current_app_user', current_user_with_revocation)
    denied = delete_client.delete(f'{base}/songs/{song_id}', headers=principals['member'])
    assert denied.status_code == 403
    assert stale_actor['id'] is not None
    with access_app.app_context():
        assert db.session.get(AppUser, stale_actor['id']).is_platform_admin is False
        assert db.session.get(Song, song_id) is not None


def test_parent_deletes_with_media_are_blocked_before_database_cascade(access_app, delete_client, signer):
    band_id = _band_id(access_app)
    principals = _principals(signer)
    with access_app.app_context():
        owner = BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').one()
        song = Song(band_id=band_id, created_by_membership_id=owner.id,
                    visibility='PUBLIC', title='parent song', artist='artist')
        rehearsal = Rehearsal(band_id=band_id, created_by_membership_id=owner.id,
                              visibility='PUBLIC', title='parent rehearsal', date=date(2026, 10, 15))
        db.session.add_all((song, rehearsal))
        db.session.flush()
        media = Media(band_id=band_id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      song_id=song.id, rehearsal_id=rehearsal.id, filename='opaque.mp4', file_type='video',
                      object_key=f'bands/{band_id}/media/opaque.mp4')
        db.session.add(media)
        db.session.commit()
        song_id, rehearsal_id, media_id = song.id, rehearsal.id, media.id

    base = f'/api/v2/bands/{band_id}'
    assert delete_client.delete(f'{base}/songs/{song_id}', headers=principals['owner']).status_code == 409
    assert delete_client.delete(f'{base}/rehearsals/{rehearsal_id}', headers=principals['owner']).status_code == 409
    with access_app.app_context():
        assert db.session.get(Song, song_id) is not None
        assert db.session.get(Rehearsal, rehearsal_id) is not None
        assert db.session.get(Media, media_id) is not None


def test_rehearsal_delete_role_matrix_and_author_edit_remains(access_app, delete_client, signer):
    band_id = _band_id(access_app)
    principals = _principals(signer)
    with access_app.app_context():
        member = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        rehearsal = Rehearsal(band_id=band_id, created_by_membership_id=member.id,
                              visibility='PUBLIC', title='member rehearsal', date=date(2026, 10, 15))
        db.session.add(rehearsal)
        db.session.commit()
        rehearsal_id = rehearsal.id
    base = f'/api/v2/bands/{band_id}/rehearsals/{rehearsal_id}'
    assert delete_client.patch(base, headers=principals['member'], json={'title': 'member edit'}).status_code == 200
    assert delete_client.delete(base, headers=principals['member']).status_code == 403
    assert delete_client.delete(base, headers=principals['admin']).status_code == 403
    assert delete_client.delete(base, headers=principals['owner']).status_code == 200


def test_comment_cascade_suggestion_proceed_and_announcement_clear_policy(access_app, delete_client, signer):
    band_id = _band_id(access_app)
    principals = _principals(signer)
    with access_app.app_context():
        owner = BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').one()
        member = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        song = Song(band_id=band_id, created_by_membership_id=owner.id,
                    visibility='PUBLIC', title='comment target', artist='artist')
        db.session.add(song)
        db.session.flush()
        media = Media(band_id=band_id, created_by_membership_id=owner.id, visibility='PUBLIC',
                      song_id=song.id, filename='comment.mp3', file_type='audio')
        suggestion = SongSuggestion(band_id=band_id, created_by_membership_id=member.id,
                                    visibility='PUBLIC', title='suggestion', artist='artist',
                                    link='https://example.com/song')
        announcement = Announcement(band_id=band_id, created_by_membership_id=member.id,
                                    visibility='PUBLIC', content='notice')
        db.session.add_all((media, suggestion, announcement))
        db.session.flush()
        parent = Comment(band_id=band_id, created_by_membership_id=member.id, media_id=media.id,
                         author='member', content='parent')
        db.session.add(parent)
        db.session.flush()
        reply = Comment(band_id=band_id, created_by_membership_id=member.id, media_id=media.id,
                        parent_id=parent.id, author='member', content='reply')
        db.session.add(reply)
        db.session.commit()
        parent_id, reply_id = parent.id, reply.id
        suggestion_id, announcement_id = suggestion.id, announcement.id

    base = f'/api/v2/bands/{band_id}'
    assert delete_client.delete(f'{base}/comments/{parent_id}', headers=principals['member']).status_code == 403
    assert delete_client.delete(f'{base}/comments/{parent_id}', headers=principals['admin']).status_code == 403
    assert delete_client.delete(f'{base}/comments/{parent_id}', headers=principals['owner']).status_code == 200
    with access_app.app_context():
        assert db.session.get(Comment, parent_id) is None
        assert db.session.get(Comment, reply_id) is None

    # An admin has song creation/proceed authority, but not the delete authority
    # needed to remove the source recommendation.
    assert delete_client.post(f'{base}/suggestions/{suggestion_id}/proceed', headers=principals['member'],
                              json={}).status_code == 403
    assert delete_client.post(f'{base}/suggestions/{suggestion_id}/proceed', headers=principals['admin'],
                              json={}).status_code == 403
    assert delete_client.post(f'{base}/suggestions/{suggestion_id}/proceed', headers=principals['owner'],
                              json={}).status_code == 201
    with access_app.app_context():
        assert db.session.get(SongSuggestion, suggestion_id) is None

    assert delete_client.put(f'{base}/announcement', headers=principals['owner'],
                             json={'content': ''}).status_code == 400
    assert delete_client.delete(f'{base}/announcement', headers=principals['member']).status_code == 403
    assert delete_client.delete(f'{base}/announcement', headers=principals['admin']).status_code == 403
    cleared = delete_client.delete(f'{base}/announcement', headers=principals['owner'])
    assert cleared.status_code == 200
    assert cleared.get_json()['announcement']['content'] == ''
    with access_app.app_context():
        retained = db.session.get(Announcement, announcement_id)
        assert retained is not None and retained.content == ''


@pytest.mark.parametrize('corruption', ('cross_band_descendant', 'wrong_target_descendant', 'cycle'))
def test_comment_delete_rejects_corrupt_cascade_descendants(
        access_app, delete_client, signer, corruption):
    band_id = _band_id(access_app)
    principals = _principals(signer)
    with access_app.app_context():
        band_a = db.session.get(Band, band_id)
        band_b = Band.query.filter_by(slug='band-b').one()
        owner_a = BandMembership.query.filter_by(band_id=band_a.id, role='owner', status='active').one()
        owner_b = BandMembership.query.filter_by(band_id=band_b.id, role='owner', status='active').one()
        song_a = Song(band_id=band_a.id, created_by_membership_id=owner_a.id,
                      visibility='PUBLIC', title='safe comment target', artist='artist')
        song_b = Song.query.filter_by(band_id=band_b.id).one()
        db.session.add(song_a)
        db.session.flush()
        media_a = Media(band_id=band_a.id, created_by_membership_id=owner_a.id,
                        visibility='PUBLIC', song_id=song_a.id,
                        filename='comment-a.mp3', file_type='audio')
        media_b = Media(band_id=band_b.id, created_by_membership_id=owner_b.id,
                        visibility='PUBLIC', song_id=song_b.id,
                        filename='comment-b.mp3', file_type='audio')
        media_other_a = Media(band_id=band_a.id, created_by_membership_id=owner_a.id,
                              visibility='PUBLIC', song_id=song_a.id,
                              filename='comment-a-other.mp3', file_type='audio')
        db.session.add_all((media_a, media_b, media_other_a))
        db.session.flush()
        parent = Comment(band_id=band_a.id, created_by_membership_id=owner_a.id,
                         media_id=media_a.id, author='owner', content='parent')
        db.session.add(parent)
        db.session.flush()

        child_band_id = band_b.id if corruption == 'cross_band_descendant' else band_a.id
        child_membership_id = owner_b.id if child_band_id == band_b.id else owner_a.id
        child_media_id = (
            media_b.id if corruption == 'cross_band_descendant'
            else media_other_a.id if corruption == 'wrong_target_descendant'
            else media_a.id
        )
        child = Comment(band_id=child_band_id, created_by_membership_id=child_membership_id,
                        media_id=child_media_id,
                        author='owner', content='corrupt child')
        db.session.add(child)
        db.session.flush()
        # Construct an out-of-band parent edge with Core SQL. The ORM tenant
        # guard correctly rejects this corruption on ordinary writes.
        db.session.execute(text('UPDATE comment SET parent_id=:parent_id WHERE id=:id'),
                           {'parent_id': parent.id, 'id': child.id})
        if corruption == 'cycle':
            db.session.execute(text('UPDATE comment SET parent_id=:parent_id WHERE id=:id'),
                               {'parent_id': child.id, 'id': parent.id})
        db.session.commit()
        parent_id, child_id = parent.id, child.id

    response = delete_client.delete(
        f'/api/v2/bands/{band_id}/comments/{parent_id}', headers=principals['owner'],
    )
    assert response.status_code == 404
    with access_app.app_context():
        assert db.session.get(Comment, parent_id) is not None
        assert db.session.get(Comment, child_id) is not None


def test_direct_suggestion_delete_is_owner_only(access_app, delete_client, signer):
    band_id = _band_id(access_app)
    principals = _principals(signer)
    with access_app.app_context():
        member = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        suggestion = SongSuggestion(band_id=band_id, created_by_membership_id=member.id,
                                    visibility='PUBLIC', title='direct delete', artist='artist',
                                    link='https://example.com/song')
        db.session.add(suggestion)
        db.session.commit()
        suggestion_id = suggestion.id
    path = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}'
    by_author = delete_client.get(f'/api/v2/bands/{band_id}/suggestions',
                                  headers=principals['member']).get_json()['suggestions'][0]
    assert by_author['can_edit'] is True
    assert by_author['can_delete'] is False and by_author['can_proceed'] is False
    by_owner = delete_client.get(f'/api/v2/bands/{band_id}/suggestions',
                                 headers=principals['owner']).get_json()['suggestions'][0]
    assert by_owner['can_delete'] is True and by_owner['can_proceed'] is True
    by_admin = delete_client.get(f'/api/v2/bands/{band_id}/suggestions',
                                 headers=principals['admin']).get_json()['suggestions'][0]
    assert by_admin['can_delete'] is False and by_admin['can_proceed'] is False
    assert delete_client.delete(path, headers=principals['member']).status_code == 403
    assert delete_client.delete(path, headers=principals['admin']).status_code == 403
    assert delete_client.delete(path, headers=principals['owner']).status_code == 200


def test_legacy_destructive_routes_are_closed_even_when_legacy_writes_enabled(
        access_app, delete_client, monkeypatch):
    access_app.config['LEGACY_V1_WRITES_ENABLED'] = True
    with access_app.app_context():
        # Existing rows prove the retired endpoint stops before lookup/storage
        # and cannot use the enabled compatibility switch as authority.
        band = Band.query.filter_by(slug='band-a').one()
        membership = BandMembership.query.filter_by(band_id=band.id, role='owner', status='active').one()
        song = Song(band_id=band.id, created_by_membership_id=membership.id,
                    visibility='PUBLIC', title='legacy protected', artist='artist')
        db.session.add(song)
        db.session.flush()
        media = Media(band_id=band.id, created_by_membership_id=membership.id, visibility='PUBLIC',
                      song_id=song.id, filename='legacy-protected.mp4', file_type='video',
                      object_key=f'bands/{band.id}/media/legacy-protected.mp4')
        db.session.add(media)
        db.session.commit()
        song_id, media_id = song.id, media.id
    from storage import storage
    storage_calls = []
    monkeypatch.setattr(storage, 'delete', lambda *args, **kwargs: storage_calls.append(args))
    monkeypatch.setattr(storage, 'copy', lambda *args, **kwargs: storage_calls.append(args))
    monkeypatch.setattr(storage, 'exists', lambda *args, **kwargs: storage_calls.append(args) or True)
    retired = (
        ('DELETE', '/gallery/999999'),
        ('DELETE', '/songs/999999'),
        ('DELETE', '/media/999999'),
        ('DELETE', '/personal-logs/999999'),
        ('DELETE', '/rehearsals/999999'),
        ('DELETE', '/members/999999'),
        ('DELETE', '/comments/999999'),
        ('DELETE', '/suggestions/999999'),
        ('PUT', '/media/999999/rename'),
        ('POST', '/suggestions/999999/promote'),
    )
    for method, path in retired:
        if path == '/songs/999999':
            path = f'/songs/{song_id}'
        elif path == '/media/999999':
            path = f'/media/{media_id}'
        elif path == '/media/999999/rename':
            path = f'/media/{media_id}/rename'
        response = delete_client.open(path, method=method, json={})
        assert response.status_code == 410, (method, path, response.status_code)
    assert storage_calls == []
    with access_app.app_context():
        assert db.session.get(Song, song_id) is not None
        assert db.session.get(Media, media_id) is not None
