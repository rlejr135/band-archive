"""Real, owned-local PostgreSQL serialization contracts for invitation mutations.

These tests deliberately observe a PostgreSQL blocking relationship before
releasing the first transaction. Thread scheduling or a fixed sleep is not
accepted as evidence that the second mutation actually competed for a lock.
"""
import hashlib
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from flask_migrate import upgrade
from sqlalchemy import event, text

import invite_link_service as service
from errors import NotFoundError
from extensions import db
from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app
from models import (AppUser, AuditEvent, Band, BandInviteLink, BandMembership,
                    InviteResumeContext, Member)
from tenant_backfill import apply as apply_tenant_backfill
from tests.test_data_api_security_migration import private_pg17  # noqa: F401


pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.skipif(os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
                       reason='explicitly provisioned owned localhost PG17 only'),
]


def _digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


@pytest.fixture
def invite_pg(private_pg17, monkeypatch):
    """Use migrations, not create_all, and never accept an external DB URL."""
    assert private_pg17.startswith('postgresql+psycopg://postgres@127.0.0.1:')
    app = create_migration_app(private_pg17)
    with app.app_context():
        upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
        with db.engine.begin() as connection:
            apply_tenant_backfill(connection)
        upgrade(revision='head', directory=str(MIGRATIONS_DIRECTORY))

        owner = AppUser(supabase_user_id='a0000000-0000-0000-0000-000000000001',
                        email='invite-owner@example.invalid', email_verified=True)
        entrant = AppUser(supabase_user_id='a0000000-0000-0000-0000-000000000002',
                          email='invite-entrant@example.invalid', email_verified=True)
        band = Band(name='Owned concurrent invite fixture', slug='owned-invite-race')
        db.session.add_all((owner, entrant, band))
        db.session.flush()
        db.session.add(BandMembership(band_id=band.id, user_id=owner.id,
                                      role='owner', status='active'))
        timestamp = datetime.now(timezone.utc)
        token, context = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        link = BandInviteLink(band_id=band.id, token_hash=_digest(token),
                              created_by_user_id=owner.id, created_at=timestamp,
                              expires_at=timestamp + timedelta(hours=24))
        db.session.add(link)
        db.session.flush()
        # Binding was already completed by /resume. These tests intentionally
        # isolate the mutation race from JWT, policy and email-claim contracts.
        resume = InviteResumeContext(
            invite_link_id=link.id, context_hash=_digest(context),
            expected_email_hmac=service._email_hmac(context, entrant.email),
            created_at=timestamp, expires_at=timestamp + timedelta(hours=1),
            claimed_user_id=entrant.id, claimed_at=timestamp,
        )
        db.session.add(resume)
        db.session.commit()
        ids = {'owner': owner.id, 'entrant': entrant.id, 'band': band.id,
               'link': link.id, 'context': resume.id, 'raw_context': context,
               'raw_token': token}
        db.session.remove()
        engine = db.engine

    # Only process-local request budgets are bypassed. The persisted cap,
    # transaction locks, inserts, constraints and service methods remain real.
    monkeypatch.setattr(service, 'rate_limit', lambda *args, **kwargs: None)
    monkeypatch.setattr(service, '_hit', lambda *args, **kwargs: None)
    with service._lock:
        service._global_context_issuance.clear()
    try:
        yield app, engine, ids
    finally:
        with service._lock:
            service._global_context_issuance.clear()
        with app.app_context():
            db.session.remove()
            engine.dispose()


def _ordered_contention(app, engine, first_operation, second_operation):
    """Run first with the band lock held; prove second is blocked behind it."""
    local = threading.local()
    first_locked, second_query, release = (threading.Event() for _ in range(3))
    pids = {}

    def band_lock(statement):
        return ('FOR UPDATE' in statement.upper()
                and re.search(r'\bFROM\s+band\b', statement, re.IGNORECASE))

    def before(connection, cursor, statement, parameters, context, executemany):
        if not band_lock(statement) or getattr(local, 'seen_band_lock', False):
            return
        local.seen_band_lock = True
        pids[local.label] = connection.connection.driver_connection.info.backend_pid
        if local.label == 'second':
            second_query.set()

    def after(connection, cursor, statement, parameters, context, executemany):
        if (getattr(local, 'label', None) == 'first' and band_lock(statement)
                and not first_locked.is_set()):
            first_locked.set()
            assert release.wait(20), 'coordinator did not release the held band lock'

    def worker(label, operation):
        local.label = label
        local.seen_band_lock = False
        with app.app_context():
            try:
                # Bound failure even if a regression deadlocks. These SETs do
                # not create serialization; the actual row locks must do that.
                db.session.execute(text("SET LOCAL statement_timeout = '15s'"))
                db.session.execute(text("SET LOCAL lock_timeout = '15s'"))
                return operation()
            except (NotFoundError, service.RateLimited) as error:
                db.session.rollback()
                return {'error_status': error.status_code}
            finally:
                db.session.remove()

    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='invite-owned-pg') as pool:
            first = pool.submit(worker, 'first', first_operation)
            try:
                assert first_locked.wait(10), 'first mutation did not take the band row lock'
                second = pool.submit(worker, 'second', second_operation)
                assert second_query.wait(10), 'second mutation did not attempt the band row lock'
                assert pids['first'] != pids['second'], 'workers must use separate PostgreSQL sessions'
                deadline = time.monotonic() + 10
                with engine.connect() as observer:
                    while True:
                        blockers = observer.execute(text('SELECT pg_blocking_pids(:pid)'),
                                                    {'pid': pids['second']}).scalar_one()
                        if pids['first'] in blockers:
                            break
                        assert time.monotonic() < deadline, 'second mutation never blocked behind first'
                release.set()
                return first.result(timeout=20), second.result(timeout=20)
            finally:
                release.set()
    finally:
        event.remove(engine, 'before_cursor_execute', before)
        event.remove(engine, 'after_cursor_execute', after)


def _accept(ids):
    return service.accept(ids['raw_context'], db.session.get(AppUser, ids['entrant']))


def _revoke(ids):
    row = service.revoke_link(db.session.get(AppUser, ids['owner']), ids['band'], ids['link'])
    return {'revoked': row.revoked_at is not None}


def _assert_membership(app, ids, *, joined):
    with app.app_context():
        memberships = BandMembership.query.filter_by(band_id=ids['band'], user_id=ids['entrant']).all()
        profiles = Member.query.filter_by(band_id=ids['band'], linked_user_id=ids['entrant']).all()
        joined_audits = AuditEvent.query.filter_by(
            band_id=ids['band'], actor_user_id=ids['entrant'],
            event_type='membership_joined_by_invite_link').all()
        context = db.session.get(InviteResumeContext, ids['context'])
        assert len(memberships) == len(profiles) == len(joined_audits) == int(joined)
        assert (context.completed_at is not None) is joined
        if joined:
            assert memberships[0].status == 'active'
            assert memberships[0].role == 'member'
            assert joined_audits[0].target_id == str(memberships[0].id)
        db.session.remove()


def test_concurrent_duplicate_accept_is_atomic_and_idempotent(invite_pg):
    app, engine, ids = invite_pg
    first, second = _ordered_contention(app, engine, lambda: _accept(ids), lambda: _accept(ids))
    assert first['joined'] is True and first['already_member'] is False
    assert second['joined'] is False and second['already_member'] is True
    assert first['band'] == second['band']
    _assert_membership(app, ids, joined=True)


@pytest.mark.parametrize('revoke_first', (True, False), ids=('revoke-wins', 'accept-wins'))
def test_revoke_and_accept_have_one_serialized_order(invite_pg, revoke_first):
    app, engine, ids = invite_pg
    first_op, second_op = ((lambda: _revoke(ids)), (lambda: _accept(ids))) if revoke_first else (
        (lambda: _accept(ids)), (lambda: _revoke(ids)))
    first, second = _ordered_contention(app, engine, first_op, second_op)
    if revoke_first:
        assert first == {'revoked': True}
        assert second == {'error_status': 404}
    else:
        assert first['joined'] is True
        assert second == {'revoked': True}
    _assert_membership(app, ids, joined=not revoke_first)
    with app.app_context():
        link = db.session.get(BandInviteLink, ids['link'])
        assert link.revoked_at is not None
        assert link.revoked_by_user_id == ids['owner']
        assert AuditEvent.query.filter_by(band_id=ids['band'], event_type='invite_link_revoked').count() == 1
        db.session.remove()


@pytest.mark.parametrize('cap', ('link', 'band'))
def test_concurrent_context_creation_cannot_exceed_persisted_live_cap(invite_pg, cap):
    app, engine, ids = invite_pg
    with app.app_context():
        # The fixture already holds one live context. The band-cap case keeps
        # every link below 200, so the per-link cap cannot mask a broken band cap.
        timestamp = datetime.now(timezone.utc)
        counts = [(ids['link'], 198 if cap == 'link' else 98)]
        if cap == 'band':
            for _ in range(5):
                link = BandInviteLink(band_id=ids['band'], token_hash=_digest(secrets.token_urlsafe(32)),
                                      created_by_user_id=ids['owner'], created_at=timestamp,
                                      expires_at=timestamp + timedelta(hours=24))
                db.session.add(link)
                db.session.flush()
                counts.append((link.id, 180))
        db.session.add_all([
            InviteResumeContext(invite_link_id=link_id, context_hash=_digest(secrets.token_urlsafe(32)),
                                expected_email_hmac='0' * 64, created_at=timestamp,
                                expires_at=timestamp + timedelta(hours=1))
            for link_id, count in counts for _ in range(count)
        ])
        db.session.commit()
        db.session.remove()

    def issue(address):
        context, expiry = service.create_resume_context(ids['raw_token'], 'new@example.invalid', address)
        assert service.canonical_token(context)
        assert expiry > timestamp
        return {'created': True}

    first, second = _ordered_contention(app, engine, lambda: issue('owned-first'), lambda: issue('owned-second'))
    assert first == {'created': True}
    assert second == {'error_status': 429}
    with app.app_context():
        live = (InviteResumeContext.expires_at > datetime.now(timezone.utc),
                InviteResumeContext.completed_at.is_(None))
        assert InviteResumeContext.query.filter_by(invite_link_id=ids['link']).filter(*live).count() == (
            200 if cap == 'link' else 100)
        expected = 200 if cap == 'link' else 1000
        assert InviteResumeContext.query.join(BandInviteLink).filter(
            BandInviteLink.band_id == ids['band'], *live).count() == expected
        assert BandMembership.query.filter_by(band_id=ids['band'], user_id=ids['entrant']).count() == 0
        db.session.remove()
