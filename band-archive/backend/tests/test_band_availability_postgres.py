"""Owned localhost PG17 schema/security and real concurrent calendar writes."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, current_thread

import pytest
from sqlalchemy import text

import availability_service as service
from errors import ValidationError
from extensions import db
from migration_bootstrap import create_migration_app, schema_contract_differences
from models import AppUser, Band, BandMembership, BandAvailabilityDay as Day, BandAvailabilityState as State
from startup_guard import assert_postgres_startup_ready

_spec = importlib.util.spec_from_file_location('calendar_owned_pg_fixture',
    Path(__file__).with_name('test_preserve_identity_refresh_postgres.py'))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
private_pg17 = _fixture.private_pg17
pytestmark = pytest.mark.skipif(os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly provisioned owned localhost PG17 only')


def test_postgres_calendar_schema_security_and_real_serialized_reset(private_pg17, monkeypatch):
    _fixture._upgrade_postgres(private_pg17)
    app = create_migration_app(private_pg17)
    with app.app_context():
        assert schema_contract_differences(db.session.connection()) == []
        db.session.rollback()
        assert_postgres_startup_ready(db.engine)
        users = [AppUser(supabase_user_id=f'calendar-{i}', email=f'calendar-{i}@example.test', email_verified=True)
                 for i in range(2)]
        band = Band(name='Calendar', slug='calendar')
        db.session.add_all([*users, band])
        db.session.flush()
        memberships = [BandMembership(band_id=band.id, user_id=user.id, status='active',
                                     role='owner' if i == 0 else 'member') for i, user in enumerate(users)]
        db.session.add_all(memberships)
        db.session.commit()
        user_ids, band_id = [u.id for u in users], band.id
        for name in ('band_availability_state', 'band_availability_day', 'band_personal_schedule_note'):
            row = db.session.execute(text('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=CAST(:name AS regclass)'),
                                     {'name': 'public.' + name}).one()
            assert row == (True, False)
        db.session.rollback()

    original_finish = service._finish
    entered, release, second_started = Event(), Event(), Event()
    def hold_first(state, changed):
        if current_thread().name.startswith('calendar-first'):
            entered.set()
            assert release.wait(10), 'owned race test release timed out'
        return original_finish(state, changed)
    monkeypatch.setattr(service, '_finish', hold_first)

    def execute(user_index, action):
        with app.app_context():
            try:
                user = db.session.get(AppUser, user_ids[user_index])
                return action(user)
            except ValidationError as error:
                db.session.rollback()
                return {'error': error.code, 'status': error.status_code}
            finally:
                db.session.remove()

    payload = {'generation': 0, 'days': [{'date': '2026-10-17', 'revision': 0,
                'morning': 'AVAILABLE', 'afternoon': 'UNKNOWN', 'evening': 'UNKNOWN'}]}
    try:
        # Two clients create an initially absent state: the band lock prevents
        # duplicate insertion and preserves each member's independent row.
        with ThreadPoolExecutor(1, thread_name_prefix='calendar-first') as first, \
             ThreadPoolExecutor(1, thread_name_prefix='calendar-second') as second:
            f1 = first.submit(execute, 0, lambda u: service.update_days(u, band_id, payload))
            assert entered.wait(10)
            def save_other(u):
                second_started.set()
                return service.update_days(u, band_id, payload)
            f2 = second.submit(execute, 1, save_other)
            assert second_started.wait(10)
            release.set()
            assert f1.result(timeout=15)['changed'] is True
            assert f2.result(timeout=15)['changed'] is True
        with app.app_context():
            assert State.query.filter_by(band_id=band_id).count() == 1
            assert Day.query.filter_by(band_id=band_id).count() == 2
        entered.clear(); release.clear(); second_started.clear()
        # Owner reset holds the same band/state lock as saves. A request from
        # the previous generation cannot resurrect data after reset commits.
        with ThreadPoolExecutor(1, thread_name_prefix='calendar-first') as first, \
             ThreadPoolExecutor(1, thread_name_prefix='calendar-second') as second:
            f1 = first.submit(execute, 0, lambda u: service.reset_band(u, band_id, {'generation': 0, 'confirm': True}))
            assert entered.wait(10)
            def stale_save(u):
                second_started.set()
                return service.update_days(u, band_id, payload)
            f2 = second.submit(execute, 1, stale_save)
            assert second_started.wait(10)
            release.set()
            assert f1.result(timeout=15)['generation'] == 1
            assert f2.result(timeout=15) == {'error': 'availability_generation_conflict', 'status': 409}
        with app.app_context():
            assert Day.query.filter_by(band_id=band_id).count() == 0
            assert_postgres_startup_ready(db.engine)
    finally:
        release.set()
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
