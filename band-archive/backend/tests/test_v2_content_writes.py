"""Contracts for the authenticated v2 core-content mutation boundary."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from extensions import db
from models import AuditEvent, Band, BandMembership, Comment, Media, MediaVote, Rehearsal, Song
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import access_app, signer  # reusable isolated tenant fixture


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


def _token_for(signer, subject, email):
    return _headers(_token(signer, sub=subject, email=email))


@pytest.fixture
def write_client(access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    return access_app.test_client()


@pytest.fixture
def principals(signer):
    return {
        'owner': _token_for(signer, '11111111-1111-1111-1111-111111111111', 'user@example.com'),
        'admin': _token_for(signer, '22222222-2222-2222-2222-222222222222', 'admin@example.com'),
        'member': _token_for(signer, '33333333-3333-3333-3333-333333333333', 'member@example.com'),
        'outsider': _token_for(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com'),
    }


def _band_id(access_app):
    with access_app.app_context():
        return Band.query.filter_by(slug='band-a').one().id


def _media(access_app, band_id, creator_id):
    with access_app.app_context():
        song = Song(band_id=band_id, created_by_membership_id=creator_id, visibility='PUBLIC', title='vote', artist='a')
        db.session.add(song); db.session.flush()
        media = Media(band_id=band_id, created_by_membership_id=creator_id, visibility='PUBLIC', song_id=song.id,
                      filename='x.mp3', file_type='audio')
        db.session.add(media); db.session.commit()
        return media.id


def test_writes_require_auth_active_membership_and_private_cache(access_app, write_client, principals):
    band_id = _band_id(access_app)
    url = f'/api/v2/bands/{band_id}/songs'
    assert write_client.post(url, json={'title': 'x', 'artist': 'y'}).status_code == 401
    denied = write_client.post(url, headers=principals['outsider'], json={'title': 'x', 'artist': 'y'})
    assert denied.status_code == 403
    assert denied.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in denied.headers['Vary']


def test_song_rehearsal_write_ownership_and_atomic_relation_scope(access_app, write_client, principals):
    band_id = _band_id(access_app)
    created = write_client.post(f'/api/v2/bands/{band_id}/songs', headers=principals['member'], json={
        'title': 'mine', 'artist': 'me', 'band_id': 999, 'created_by_membership_id': 999,
    })
    assert created.status_code == 201
    song_id = created.get_json()['song']['id']
    assert write_client.patch(f'/api/v2/bands/{band_id}/songs/{song_id}', headers=principals['owner'], json={
        'title': 'managed',
    }).status_code == 200
    rehearsal = write_client.post(f'/api/v2/bands/{band_id}/rehearsals', headers=principals['member'], json={
        'title': 'practice', 'date': '2026-09-27', 'song_ids': [song_id],
    })
    assert rehearsal.status_code == 201
    rehearsal_id = rehearsal.get_json()['rehearsal']['id']
    with access_app.app_context():
        foreign = Song.query.filter_by(title='B public').one()
    bad = write_client.patch(f'/api/v2/bands/{band_id}/rehearsals/{rehearsal_id}', headers=principals['member'], json={
        'song_ids': [foreign.id],
    })
    assert bad.status_code == 404
    with access_app.app_context():
        assert [item.id for item in db.session.get(Rehearsal, rehearsal_id).songs] == [song_id]


def test_account_comments_ignore_password_and_are_owner_or_manager_only(access_app, write_client, principals):
    band_id = _band_id(access_app)
    with access_app.app_context():
        owner = BandMembership.query.filter_by(band_id=band_id, role='owner').one()
    media_id = _media(access_app, band_id, owner.id)
    created = write_client.post(f'/api/v2/bands/{band_id}/media/{media_id}/comments', headers=principals['member'], json={
        'content': 'account comment', 'author': 'spoofed', 'password': 'not-used',
    })
    assert created.status_code == 201
    comment = created.get_json()['comment']
    assert comment['author'] != 'spoofed'
    comment_id = comment['id']
    with access_app.app_context():
        assert db.session.get(Comment, comment_id).password_hash is None
    assert write_client.patch(f'/api/v2/bands/{band_id}/comments/{comment_id}', headers=principals['outsider'],
                             json={'content': 'no'}).status_code == 403
    assert write_client.patch(f'/api/v2/bands/{band_id}/comments/{comment_id}', headers=principals['admin'],
                             json={'content': 'moderated'}).status_code == 200


def test_media_votes_are_membership_scoped_idempotent_and_audited(access_app, write_client, principals):
    band_id = _band_id(access_app)
    with access_app.app_context():
        owner = BandMembership.query.filter_by(band_id=band_id, role='owner').one()
    media_id = _media(access_app, band_id, owner.id)
    url = f'/api/v2/bands/{band_id}/media/{media_id}/vote'
    assert write_client.put(url, headers=principals['member'], json={'value': 1}).status_code == 200
    repeat = write_client.patch(url, headers=principals['member'], json={'value': 1})
    assert repeat.status_code == 200
    assert repeat.get_json()['media']['vote_score'] == 1
    assert write_client.put(url, headers=principals['member'], json={'value': 0}).get_json()['media']['vote_score'] == 0
    with access_app.app_context():
        assert MediaVote.query.filter_by(media_id=media_id).count() == 0
        assert AuditEvent.query.filter_by(event_type='content.media_vote.changed').count() == 3
