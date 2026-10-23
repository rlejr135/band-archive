"""Contracts for the authenticated v2 core-content mutation boundary."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.dialects import postgresql

from extensions import db
from models import AppUser, AuditEvent, Band, BandMembership, Comment, Media, MediaVote, Rehearsal, Song
from app import create_app
from tests.test_auth import AuthTestBootstrapConfig, _install_jwks, _jwk, _token
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


def test_rehearsal_type_is_optional_validated_and_serialized(access_app, write_client, principals):
    band_id = _band_id(access_app)
    base = f'/api/v2/bands/{band_id}/rehearsals'
    defaulted = write_client.post(base, headers=principals['member'], json={
        'title': 'Unclassified', 'date': '2026-10-25',
    })
    assert defaulted.status_code == 201
    defaulted_item = defaulted.get_json()['rehearsal']
    assert defaulted_item['rehearsal_type'] == 'UNCLASSIFIED'

    created = write_client.post(base, headers=principals['member'], json={
        'title': 'Live show', 'date': '2026-10-26', 'rehearsal_type': 'LIVE',
    })
    assert created.status_code == 201
    item = created.get_json()['rehearsal']
    assert item['rehearsal_type'] == 'LIVE'
    rehearsal_id = item['id']
    assert write_client.post(base, headers=principals['member'], json={
        'title': 'Invalid type', 'date': '2026-10-27', 'rehearsal_type': 'CONCERT',
    }).status_code == 400

    updated = write_client.patch(f'{base}/{rehearsal_id}', headers=principals['member'], json={
        'title': 'Renamed live show',
    })
    assert updated.status_code == 200
    assert updated.get_json()['rehearsal']['rehearsal_type'] == 'LIVE'
    assert write_client.patch(f'{base}/{rehearsal_id}', headers=principals['member'], json={
        'rehearsal_type': 'CONCERT',
    }).status_code == 400

    listed = write_client.get(base, headers=principals['member']).get_json()['rehearsals']
    assert next(row for row in listed if row['id'] == rehearsal_id)['rehearsal_type'] == 'LIVE'
    detail = write_client.get(f'{base}/{rehearsal_id}', headers=principals['member']).get_json()['rehearsal']
    assert detail['rehearsal_type'] == 'LIVE'
    dashboard = write_client.get(f'/api/v2/bands/{band_id}/dashboard?month=2026-10',
                                 headers=principals['member']).get_json()['dashboard']
    dashboard_item = next(row for row in dashboard['month_rehearsals']['items']
                          if row['id'] == rehearsal_id)
    assert dashboard_item['rehearsal_type'] == 'LIVE'


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


def test_file_sqlite_concurrent_account_votes_preserve_v1_totals(tmp_path, signer):
    """One membership can race itself without duplicate rows or lost totals."""
    db_path = tmp_path / 'v2-account-votes.db'

    class FileAuthConfig(AuthTestBootstrapConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path.as_posix()}'

    app = create_app(FileAuthConfig, start_worker=False)
    try:
        with app.app_context():
            db.drop_all(); db.create_all()
            user = AppUser(
                supabase_user_id='11111111-1111-1111-1111-111111111111', email='user@example.com',
            )
            band = Band(name='votes', slug='votes', default_visibility='PUBLIC')
            db.session.add_all((user, band)); db.session.flush()
            membership = BandMembership(band_id=band.id, user_id=user.id, role='member', status='active')
            db.session.add(membership); db.session.flush()
            song = Song(band_id=band.id, created_by_membership_id=membership.id,
                        visibility='PUBLIC', title='song', artist='artist')
            db.session.add(song); db.session.flush()
            media = Media(band_id=band.id, created_by_membership_id=membership.id, visibility='PUBLIC',
                          song_id=song.id, filename='clip.mp3', file_type='audio',
                          upvote_count=1, downvote_count=0, vote_score=1)
            db.session.add(media); db.session.flush()
            # A legacy device identity is deliberately not merged into the
            # account identity and remains after the account vote is removed.
            db.session.add(MediaVote(media_id=media.id, band_id=band.id, voter_hash='d' * 64,
                                     value=1, is_legacy=True))
            db.session.commit()
            band_id, media_id, membership_id = band.id, media.id, membership.id
            app.config['BAND_API_SCHEMA_READY'] = True
        app.config['SUPABASE_AUTH_ENABLED'] = True
        _install_jwks(app, [[_jwk(signer, 'key-1')]])
        headers = _token_for(signer, '11111111-1111-1111-1111-111111111111', 'user@example.com')
        url = f'/api/v2/bands/{band_id}/media/{media_id}/vote'

        def vote(value):
            with app.test_client() as client:
                return client.put(url, headers=headers, json={'value': value}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(vote, (1, 1))) == [200, 200]
        with app.app_context():
            media = db.session.get(Media, media_id)
            account_votes = MediaVote.query.filter_by(media_id=media_id, created_by_membership_id=membership_id).all()
            assert len(account_votes) == 1 and account_votes[0].value == 1
            assert (media.upvote_count, media.downvote_count, media.vote_score) == (2, 0, 2)

        # Change/remove racing each other leaves one authoritative final
        # state, and the preserved v1 device vote is always still counted.
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(vote, (-1, 0))) == [200, 200]
        with app.app_context():
            media = db.session.get(Media, media_id)
            account_votes = MediaVote.query.filter_by(media_id=media_id, created_by_membership_id=membership_id).all()
            assert len(account_votes) <= 1
            account_value = account_votes[0].value if account_votes else 0
            assert (media.upvote_count, media.downvote_count, media.vote_score) == (
                1 + int(account_value == 1), int(account_value == -1), 1 + account_value,
            )
            assert MediaVote.query.filter_by(media_id=media_id, voter_hash='d' * 64).one().value == 1
    finally:
        with app.app_context():
            db.session.remove(); db.drop_all()


def test_v2_vote_parent_lock_compiles_for_postgresql(access_app):
    """The portable SQLite lock has a real row-lock counterpart on Postgres."""
    with access_app.app_context():
        statement = Media.query.filter(Media.id == 1, Media.band_id == 1).with_for_update().statement
        assert 'FOR UPDATE' in str(statement.compile(dialect=postgresql.dialect()))
