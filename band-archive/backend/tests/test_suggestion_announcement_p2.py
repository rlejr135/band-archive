"""P2 race and permission coverage for the v08 suggestion/announcement writes.

The production routes are deliberately exercised through the authenticated
HTTP boundary.  In particular, these checks do not use the legacy password
suggestion endpoints as a shortcut.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.dialects import postgresql

from app import create_app
from extensions import db
from models import (Announcement, AppUser, AuditEvent, Band, BandMembership,
                    Song, SongSuggestion, SongSuggestionVote)
from tests.test_auth import AuthTestBootstrapConfig, _install_jwks, _jwk
from tests.test_band_access_phase6 import access_app, signer
from tests.test_v2_content_writes import _band_id, _token_for, principals, write_client


def _candidate(client, band_id, headers, *, title='candidate'):
    response = client.post(f'/api/v2/bands/{band_id}/suggestions', headers=headers, json={
        'title': title, 'artist': 'artist', 'link': 'https://example.test/candidate',
        'memo': 'keep the exact reviewed fields', 'visibility': 'MEMBERS',
    })
    assert response.status_code == 201
    return response.get_json()['suggestion']['id']


def test_suggestion_rbac_is_author_scoped_and_never_falls_back_to_password(
        access_app, write_client, principals, signer):
    band_id = _band_id(access_app)
    with access_app.app_context():
        other_user = AppUser(supabase_user_id='88888888-8888-8888-8888-888888888888', email='other@example.com')
        other_band = Band(name='Other', slug='other-band', default_visibility='PUBLIC')
        db.session.add_all((other_user, other_band)); db.session.flush()
        db.session.add(BandMembership(band_id=band_id, user_id=other_user.id, role='member', status='active'))
        db.session.add(BandMembership(band_id=other_band.id, user_id=other_user.id, role='owner', status='active'))
        db.session.commit()
        other_band_id = other_band.id
    other = _token_for(signer, '88888888-8888-8888-8888-888888888888', 'other@example.com')
    suggestion_id = _candidate(write_client, band_id, principals['member'])
    path = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}'

    # A different ordinary member cannot adopt a current member's row; a
    # manager can proceed it but cannot edit/delete it either.
    assert write_client.patch(path, headers=other, json={'title': 'no'}).status_code == 403
    assert write_client.delete(path, headers=principals['admin']).status_code == 403
    assert write_client.patch(path, headers=principals['member'], json={
        'title': 'author edit', 'password': 'ignored-legacy-secret',
    }).status_code == 400
    assert write_client.patch(path, headers=principals['member'], json={'title': 'author edit'}).status_code == 200
    assert write_client.post(f'{path}/proceed', headers=principals['member'], json={'password': 'admin'}).status_code == 400
    assert write_client.post(f'{path}/proceed', headers=principals['admin'], json={'visibility': 'PUBLIC'}).status_code == 201

    # Object ids are tenant-bound: another band never learns whether this id
    # exists.  Announcement writes remain manager-only and have no password
    # compatibility parameter.
    assert write_client.get(f'/api/v2/bands/{other_band_id}/suggestions/{suggestion_id}', headers=other).status_code == 405
    assert write_client.patch(f'/api/v2/bands/{other_band_id}/suggestions/{suggestion_id}', headers=other,
                              json={'title': 'cross band'}).status_code == 404
    announcement = f'/api/v2/bands/{band_id}/announcement'
    assert write_client.put(announcement, headers=principals['member'], json={'content': 'no'}).status_code == 403
    assert write_client.put(announcement, headers=principals['owner'], json={'content': 'owner notice', 'password': 'admin'}).status_code == 400
    assert write_client.put(announcement, headers=principals['owner'], json={'content': 'owner notice'}).status_code == 200

    # Legacy NULL authors are the one intentional manager stewardship case.
    with access_app.app_context():
        legacy = SongSuggestion(band_id=band_id, visibility='MEMBERS', title='legacy', artist='artist',
                                link='https://example.test/legacy')
        db.session.add(legacy); db.session.commit(); legacy_id = legacy.id
    legacy_path = f'/api/v2/bands/{band_id}/suggestions/{legacy_id}'
    assert write_client.patch(legacy_path, headers=principals['member'], json={'title': 'blocked'}).status_code == 403
    assert write_client.patch(legacy_path, headers=principals['admin'], json={'title': 'admin keeps queue'}).status_code == 200


def test_sqlite_concurrent_suggestion_votes_are_account_owned_and_keep_totals(tmp_path, signer):
    """Same-account races converge; another account remains an independent vote."""
    db_path = tmp_path / 'suggestion-votes.db'

    class FileConfig(AuthTestBootstrapConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path.as_posix()}'

    app = create_app(FileConfig, start_worker=False)
    try:
        with app.app_context():
            db.drop_all(); db.create_all()
            first = AppUser(supabase_user_id='11111111-1111-1111-1111-111111111111', email='one@example.com')
            second = AppUser(supabase_user_id='22222222-2222-2222-2222-222222222222', email='two@example.com')
            band = Band(name='votes', slug='suggestion-votes', default_visibility='PUBLIC')
            db.session.add_all((first, second, band)); db.session.flush()
            one_membership = BandMembership(band_id=band.id, user_id=first.id, role='member', status='active')
            two_membership = BandMembership(band_id=band.id, user_id=second.id, role='member', status='active')
            db.session.add_all((one_membership, two_membership)); db.session.flush()
            suggestion = SongSuggestion(band_id=band.id, created_by_membership_id=one_membership.id,
                                        visibility='PUBLIC', title='race', artist='artist',
                                        link='https://example.test/race')
            db.session.add(suggestion); db.session.commit()
            band_id, suggestion_id, one_id, two_id = band.id, suggestion.id, first.id, second.id
            app.config['BAND_API_SCHEMA_READY'] = True
        app.config['SUPABASE_AUTH_ENABLED'] = True
        _install_jwks(app, [[_jwk(signer, 'key-1')]])
        one_headers = _token_for(signer, '11111111-1111-1111-1111-111111111111', 'one@example.com')
        two_headers = _token_for(signer, '22222222-2222-2222-2222-222222222222', 'two@example.com')
        path = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}/vote'

        def vote(headers, value):
            with app.test_client() as client:
                return client.put(path, headers=headers, json={'value': value}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda value: vote(one_headers, value), (1, 1))) == [200, 200]
        with app.app_context():
            rows = SongSuggestionVote.query.filter_by(suggestion_id=suggestion_id, voter_user_id=one_id).all()
            item = db.session.get(SongSuggestion, suggestion_id)
            assert len(rows) == 1 and rows[0].value == 1
            assert (item.thumbs_up, item.thumbs_down) == (1, 0)

        # Competing change/remove requests cannot create a second voter row or
        # lose the aggregate for the final row state.
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(lambda value: vote(one_headers, value), (-1, 0))) == [200, 200]
        assert vote(two_headers, 1) == 200
        with app.app_context():
            rows = SongSuggestionVote.query.filter_by(suggestion_id=suggestion_id, is_legacy=False).all()
            one_rows = [row for row in rows if row.voter_user_id == one_id]
            two_rows = [row for row in rows if row.voter_user_id == two_id]
            assert len(one_rows) <= 1 and len(two_rows) == 1 and two_rows[0].value == 1
            one_value = one_rows[0].value if one_rows else 0
            item = db.session.get(SongSuggestion, suggestion_id)
            assert (item.thumbs_up, item.thumbs_down) == (1 + int(one_value == 1), int(one_value == -1))
    finally:
        with app.app_context():
            db.session.remove(); db.drop_all()


def test_sqlite_concurrent_proceed_has_one_winner_and_one_audit(tmp_path, signer):
    db_path = tmp_path / 'suggestion-proceed.db'

    class FileConfig(AuthTestBootstrapConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path.as_posix()}'

    app = create_app(FileConfig, start_worker=False)
    try:
        with app.app_context():
            db.drop_all(); db.create_all()
            user = AppUser(supabase_user_id='11111111-1111-1111-1111-111111111111', email='owner@example.com')
            band = Band(name='proceed', slug='suggestion-proceed', default_visibility='MEMBERS')
            db.session.add_all((user, band)); db.session.flush()
            membership = BandMembership(band_id=band.id, user_id=user.id, role='owner', status='active')
            db.session.add(membership); db.session.flush()
            candidate = SongSuggestion(band_id=band.id, created_by_membership_id=membership.id,
                                       visibility='MEMBERS', title='one winner', artist='artist',
                                       link='https://example.test/winner', memo='source memo')
            db.session.add(candidate); db.session.commit()
            band_id, suggestion_id = band.id, candidate.id
            app.config['BAND_API_SCHEMA_READY'] = True
        app.config['SUPABASE_AUTH_ENABLED'] = True
        _install_jwks(app, [[_jwk(signer, 'key-1')]])
        headers = _token_for(signer, '11111111-1111-1111-1111-111111111111', 'owner@example.com')
        path = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}/proceed'

        def proceed():
            with app.test_client() as client:
                return client.post(path, headers=headers, json={'visibility': 'PUBLIC'}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: proceed(), range(2)))
        assert sorted(results) == [201, 404]
        with app.app_context():
            songs = Song.query.filter_by(band_id=band_id, title='one winner').all()
            assert len(songs) == 1 and songs[0].visibility == 'PUBLIC' and songs[0].memo == 'source memo'
            assert db.session.get(SongSuggestion, suggestion_id) is None
            assert AuditEvent.query.filter_by(event_type='content.suggestion.proceeded').count() == 1
    finally:
        with app.app_context():
            db.session.remove(); db.drop_all()


def test_proceed_failed_transaction_keeps_the_candidate(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    rollback_id = _candidate(write_client, band_id, principals['member'], title='must roll back')
    import routes.content_writes as content_writes
    original_audit = content_writes.audit
    monkeypatch.setattr(content_writes, 'audit', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('inject')))
    with pytest.raises(RuntimeError):
        write_client.post(f'/api/v2/bands/{band_id}/suggestions/{rollback_id}/proceed',
                          headers=principals['owner'], json={})
    monkeypatch.setattr(content_writes, 'audit', original_audit)
    with access_app.app_context():
        assert SongSuggestion.query.get(rollback_id) is not None
        assert Song.query.filter_by(band_id=band_id, title='must roll back').count() == 0


def test_suggestion_lock_and_unique_index_compile_for_postgresql(access_app):
    with access_app.app_context():
        lock = SongSuggestion.query.filter_by(id=1, band_id=1).with_for_update().statement
        assert 'FOR UPDATE' in str(lock.compile(dialect=postgresql.dialect()))
        index = next(item for item in SongSuggestionVote.__table__.indexes
                     if item.name == 'uq_song_suggestion_vote_user')
        assert tuple(column.name for column in index.columns) == ('suggestion_id', 'voter_user_id')
        assert 'voter_user_id IS NOT NULL' in str(index.dialect_options['postgresql']['where'])
