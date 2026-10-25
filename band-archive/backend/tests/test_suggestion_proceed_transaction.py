"""Existing v2 song-suggestion proceed transaction and retry contracts."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
import os
import time

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app import create_app
from extensions import db
from models import AppUser, AuditEvent, Band, BandMembership, Song, SongSuggestion, SongSuggestionVote
from tests.test_auth import AuthTestBootstrapConfig, _install_jwks, _jwk
from tests.test_band_access_phase6 import access_app, signer
from tests.test_preserve_identity_refresh_postgres import private_pg17
from tests.test_v2_content_writes import _band_id, _token_for, principals, write_client


def _seed_candidate(app, band_id, title, *, creator_role='member'):
    with app.app_context():
        creator = BandMembership.query.filter_by(band_id=band_id, role=creator_role, status='active').one()
        candidate = SongSuggestion(
            band_id=band_id, created_by_membership_id=creator.id, visibility='MEMBERS',
            title=title, artist='artist', link='https://example.test/reviewed', memo='reviewed memo',
            thumbs_up=1,
        )
        db.session.add(candidate)
        db.session.flush()
        vote = SongSuggestionVote(
            band_id=band_id, suggestion_id=candidate.id, voter_user_id=creator.user_id,
            created_by_membership_id=creator.id, is_legacy=False, value=1,
        )
        db.session.add(vote)
        db.session.commit()
        return candidate.id


def _assert_business_rows(app, band_id, suggestion_id, title, *, committed):
    with app.app_context():
        db.session.remove()
        songs = Song.query.filter_by(band_id=band_id, title=title).all()
        suggestion = db.session.get(SongSuggestion, suggestion_id)
        votes = SongSuggestionVote.query.filter_by(suggestion_id=suggestion_id).all()
        audits = AuditEvent.query.filter_by(event_type='content.suggestion.proceeded').all()
        if committed:
            assert len(songs) == len(audits) == 1
            assert suggestion is None and votes == []
            assert audits[0].target_id == str(songs[0].id)
        else:
            assert songs == [] and audits == []
            assert suggestion is not None and len(votes) == 1 and votes[0].value == 1
            assert suggestion.thumbs_up == 1


def _enable_sqlite_foreign_keys(app):
    """Match PostgreSQL's enforced ON DELETE CASCADE for this SQLite contract test."""
    with app.app_context():
        db.session.remove()
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        assert db.session.execute(text('PRAGMA foreign_keys')).scalar_one() == 1
        db.session.remove()


@pytest.mark.parametrize('failure', ('song_flush', 'audit_flush', 'final_commit', 'serializer', 'jsonify'))
def test_proceed_failure_rolls_back_song_suggestion_vote_and_audit(
        access_app, write_client, principals, monkeypatch, failure):
    import routes.content_writes as writes

    band_id = _band_id(access_app)
    title = f'rollback-{failure}'
    suggestion_id = _seed_candidate(access_app, band_id, title)
    with access_app.app_context():
        owner = AppUser.query.filter_by(supabase_user_id='11111111-1111-1111-1111-111111111111').one()
        owner.email_verified = False
        db.session.commit()
    path = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}/proceed'
    phases = []
    entry_transactions = []
    original_proceed = writes.proceed_song_suggestion

    def observed_proceed(**kwargs):
        session = db.session()
        entry_transactions.append(session.in_transaction())
        session.info['proceed_under_test'] = True
        try:
            return original_proceed(**kwargs)
        finally:
            session.info.pop('proceed_under_test', None)

    def before_flush(session, flush_context, instances):
        if failure == 'song_flush' and any(isinstance(row, Song) and row.title == title for row in session.new):
            raise RuntimeError('injected Song flush failure')
        if failure == 'audit_flush' and any(
                isinstance(row, AuditEvent) and row.event_type == 'content.suggestion.proceeded'
                for row in session.new):
            raise RuntimeError('injected audit flush failure')

    def before_commit(session):
        business = session.info.get('proceed_under_test', False)
        phases.append('business' if business else 'auth')
        if failure == 'final_commit' and business:
            raise RuntimeError('injected business commit failure')

    def fail_serializer(*args, **kwargs):
        raise RuntimeError('injected serializer failure')

    def fail_jsonify(*args, **kwargs):
        raise RuntimeError('injected jsonify failure')

    monkeypatch.setattr(writes, 'proceed_song_suggestion', observed_proceed)
    if failure == 'serializer':
        monkeypatch.setattr(writes, 'serialize_song', fail_serializer)
    if failure == 'jsonify':
        monkeypatch.setattr(writes, 'jsonify', fail_jsonify)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected'):
            write_client.post(path, headers=principals['owner'], json={'visibility': 'PUBLIC'})
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert len(entry_transactions) == 1 and isinstance(entry_transactions[0], bool)
    assert phases[0] == 'auth'
    assert phases.count('business') == (1 if failure == 'final_commit' else 0)
    # The authentication synchronization commit is independent of the failed business write.
    with access_app.app_context():
        db.session.remove()
        assert AppUser.query.filter_by(
            supabase_user_id='11111111-1111-1111-1111-111111111111').one().email_verified is True
    _assert_business_rows(access_app, band_id, suggestion_id, title, committed=False)


def test_proceed_commits_before_response_can_be_lost_and_retry_is_404(
        access_app, write_client, principals):
    _enable_sqlite_foreign_keys(access_app)
    band_id = _band_id(access_app)
    title = 'response-lost-after-commit'
    suggestion_id = _seed_candidate(access_app, band_id, title)
    path = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}/proceed'

    response = write_client.post(path, headers=principals['owner'], json={'visibility': 'PUBLIC'})
    assert response.status_code == 201
    payload = response.get_json()
    assert payload['proceeded'] is True
    assert payload['song']['title'] == title
    assert payload['song']['artist'] == 'artist'
    assert payload['song']['memo'] == 'reviewed memo'
    assert payload['song']['visibility'] == 'PUBLIC'
    assert payload['song']['status'] == 'Practice'
    # Simulate a client that never receives/uses the response after the server committed.
    del response, payload
    _assert_business_rows(access_app, band_id, suggestion_id, title, committed=True)
    retry = write_client.post(path, headers=principals['owner'], json={'visibility': 'PUBLIC'})
    assert retry.status_code == 404
    _assert_business_rows(access_app, band_id, suggestion_id, title, committed=True)


@pytest.mark.skipif(os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
                    reason='explicitly opt into the owned localhost PostgreSQL 17 fixture')
def test_private_postgres_two_connections_proceed_once(private_pg17, signer):
    """Opt-in owned PG17 test; runner must report zero skipped for this node."""
    from tests.test_preserve_identity_refresh_postgres import _upgrade_postgres

    _upgrade_postgres(private_pg17)
    config = type('PrivateProceedConfig', (AuthTestBootstrapConfig,), {
        'SQLALCHEMY_DATABASE_URI': private_pg17,
        'SQLALCHEMY_ENGINE_OPTIONS': {
            'connect_args': {'options': '-c lock_timeout=5s -c statement_timeout=20s'},
        },
    })
    app = create_app(config, start_worker=False)
    app.config['BAND_API_SCHEMA_READY'] = True
    app.config['SUPABASE_AUTH_ENABLED'] = True
    with app.app_context():
        user = AppUser(supabase_user_id='11111111-1111-1111-1111-111111111111',
                       email='owner@example.com', email_verified=True)
        band = Band(name='Proceed PG', slug='proceed-pg', default_visibility='MEMBERS')
        db.session.add_all((user, band))
        db.session.flush()
        membership = BandMembership(band_id=band.id, user_id=user.id, role='owner', status='active')
        db.session.add(membership)
        db.session.flush()
        candidate = SongSuggestion(band_id=band.id, created_by_membership_id=membership.id,
                                   visibility='MEMBERS', title='pg-one-winner', artist='artist',
                                   link='https://example.test/reviewed', memo='reviewed memo', thumbs_up=1)
        db.session.add(candidate)
        db.session.flush()
        db.session.add(SongSuggestionVote(band_id=band.id, suggestion_id=candidate.id,
                                          voter_user_id=user.id, created_by_membership_id=membership.id,
                                          is_legacy=False, value=1))
        db.session.commit()
        band_id, suggestion_id = band.id, candidate.id
        db.session.remove()
    _install_jwks(app, [[_jwk(signer, 'key-1')]])
    headers = _token_for(signer, '11111111-1111-1111-1111-111111111111', 'owner@example.com')
    path = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}/proceed'
    start = Barrier(2)
    before_band_lock = Barrier(2)
    pids = []
    pid_lock = Lock()

    def observe_band_lock(connection, cursor, statement, parameters, context, executemany):
        normalized = ' '.join(statement.lower().split())
        if ' from band ' not in normalized or ' for update' not in normalized:
            return
        # Each request has a checked-out connection before either attempts the first row lock.
        pid = connection.exec_driver_sql('select pg_backend_pid()').scalar_one()
        with pid_lock:
            pids.append(pid)
        before_band_lock.wait(timeout=10)

    def proceed():
        start.wait(timeout=10)
        with app.test_client() as client:
            return client.post(path, headers=headers, json={'visibility': 'PUBLIC'}).status_code

    try:
        with app.app_context():
            engine = db.engine
        event.listen(engine, 'before_cursor_execute', observe_band_lock)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: proceed(), range(2)))
        finally:
            event.remove(engine, 'before_cursor_execute', observe_band_lock)
        assert sorted(outcomes) == [201, 404]
        assert len(pids) == 2 and pids[0] != pids[1]
        _assert_business_rows(app, band_id, suggestion_id, 'pg-one-winner', committed=True)
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


@pytest.mark.skipif(os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
                    reason='explicitly opt into the owned localhost PostgreSQL 17 fixture')
@pytest.mark.parametrize('authority_change', ('role_admin', 'membership_left', 'actor_disabled'))
def test_private_postgres_proceed_rechecks_authority_after_waiting_for_band_lock(
        private_pg17, signer, authority_change):
    """The proceed request must read current authority after a concurrent committed change."""
    from tests.test_preserve_identity_refresh_postgres import _upgrade_postgres

    _upgrade_postgres(private_pg17)
    config = type('PrivateProceedAuthorityConfig', (AuthTestBootstrapConfig,), {
        'SQLALCHEMY_DATABASE_URI': private_pg17,
        'SQLALCHEMY_ENGINE_OPTIONS': {
            'connect_args': {'options': '-c lock_timeout=5s -c statement_timeout=20s'},
        },
    })
    app = create_app(config, start_worker=False)
    app.config['BAND_API_SCHEMA_READY'] = True
    app.config['SUPABASE_AUTH_ENABLED'] = True
    with app.app_context():
        user = AppUser(supabase_user_id='11111111-1111-1111-1111-111111111111',
                       email='owner@example.com', email_verified=True)
        band = Band(name='Proceed Authority', slug='proceed-authority', default_visibility='MEMBERS')
        db.session.add_all((user, band))
        db.session.flush()
        membership = BandMembership(band_id=band.id, user_id=user.id, role='owner', status='active')
        db.session.add(membership)
        db.session.commit()
        band_id, user_id, membership_id = band.id, user.id, membership.id
        db.session.remove()
    title = f'authority-{authority_change}'
    suggestion_id = _seed_candidate(app, band_id, title, creator_role='owner')
    _install_jwks(app, [[_jwk(signer, 'key-1')]])
    headers = _token_for(signer, '11111111-1111-1111-1111-111111111111', 'owner@example.com')
    path = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}/proceed'
    arrived = Event()
    request_pid = []

    def observe_band_lock(connection, cursor, statement, parameters, context, executemany):
        normalized = ' '.join(statement.lower().split())
        if ' from band ' in normalized and ' for update' in normalized:
            request_pid.append(connection.exec_driver_sql('select pg_backend_pid()').scalar_one())
            arrived.set()

    def proceed():
        with app.test_client() as client:
            return client.post(path, headers=headers, json={}).status_code

    try:
        with app.app_context():
            engine = db.engine
        with engine.connect() as blocker, engine.connect() as observer:
            transaction = blocker.begin()
            blocker.execute(text('SELECT id FROM band WHERE id=:id FOR UPDATE'), {'id': band_id}).scalar_one()
            event.listen(engine, 'before_cursor_execute', observe_band_lock)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(proceed)
                    try:
                        assert arrived.wait(timeout=10), 'proceed did not reach band row lock'
                        assert len(request_pid) == 1
                        deadline = time.monotonic() + 10
                        while True:
                            waiting = observer.execute(text(
                                'SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'
                            ), {'pid': request_pid[0]}).scalar_one_or_none()
                            observer.rollback()  # Start a fresh statistics snapshot on the next poll.
                            if waiting == 'Lock':
                                break
                            assert time.monotonic() < deadline, f'proceed did not wait on band lock: {waiting}'
                            time.sleep(0.01)
                        blocker.execute(text(
                            'SELECT id FROM band_membership WHERE id=:id FOR UPDATE'
                        ), {'id': membership_id}).scalar_one()
                        blocker.execute(text('SELECT id FROM app_user WHERE id=:id FOR UPDATE'),
                                        {'id': user_id}).scalar_one()
                        if authority_change == 'role_admin':
                            blocker.execute(text("UPDATE band_membership SET role='admin' WHERE id=:id"),
                                            {'id': membership_id})
                        elif authority_change == 'membership_left':
                            blocker.execute(text("UPDATE band_membership SET status='left' WHERE id=:id"),
                                            {'id': membership_id})
                        else:
                            blocker.execute(text("UPDATE app_user SET status='disabled' WHERE id=:id"),
                                            {'id': user_id})
                        transaction.commit()
                        assert future.result(timeout=10) == 403
                    finally:
                        # Release the row lock before the executor waits for a blocked request.
                        if transaction.is_active:
                            transaction.rollback()
            finally:
                event.remove(engine, 'before_cursor_execute', observe_band_lock)
        _assert_business_rows(app, band_id, suggestion_id, title, committed=False)
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
