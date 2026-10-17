"""Real PostgreSQL band-lock serialization for automatic profile creation."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, current_thread

import pytest
from sqlalchemy import text
import band_service
from extensions import db
from migration_bootstrap import create_migration_app, schema_contract_differences
from models import AppUser, Band, BandMembership, Member
from startup_guard import assert_postgres_startup_ready

_spec = importlib.util.spec_from_file_location('profiles_owned_pg_fixture',
    Path(__file__).with_name('test_preserve_identity_refresh_postgres.py'))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
private_pg17 = _fixture.private_pg17
pytestmark = pytest.mark.skipif(os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly provisioned owned localhost PG17 only')


def test_profile_concurrent_approval_has_one_linked_record(private_pg17, monkeypatch):
    _fixture._upgrade_postgres(private_pg17)
    app = create_migration_app(private_pg17)
    with app.app_context():
        assert schema_contract_differences(db.session.connection()) == []
        db.session.rollback()
        assert_postgres_startup_ready(db.engine)
        users = [AppUser(supabase_user_id=f'profile-{i}', email=f'p{i}@example.test',
                         email_verified=True) for i in range(2)]
        band = Band(name='Profiles', slug='profiles')
        db.session.add_all([*users, band]); db.session.flush()
        memberships = [BandMembership(band_id=band.id, user_id=u.id,
            status='active' if i == 0 else 'pending', role='owner' if i == 0 else 'member')
            for i, u in enumerate(users)]
        db.session.add_all(memberships); db.session.commit()
        actor_id, target_id, user_id, band_id = users[0].id, memberships[1].id, users[1].id, band.id
        security_before = db.session.execute(text("SELECT relrowsecurity,relforcerowsecurity,relacl::text "
            "FROM pg_class WHERE oid='public.member'::regclass")).one()
        db.session.rollback()
    entered, release, second_started = Event(), Event(), Event()
    original = band_service.ensure_member_profile
    def hold_first(membership):
        if current_thread().name.startswith('profile-first'):
            entered.set()
            assert release.wait(10)
        return original(membership)
    monkeypatch.setattr(band_service, 'ensure_member_profile', hold_first)
    def approve(second=False):
        with app.app_context():
            try:
                if second: second_started.set()
                return band_service.approve_membership(db.session.get(AppUser, actor_id), target_id, True)[1]
            finally:
                db.session.remove()
    try:
        with ThreadPoolExecutor(1, thread_name_prefix='profile-first') as first, \
             ThreadPoolExecutor(1, thread_name_prefix='profile-second') as second:
            a = first.submit(approve)
            assert entered.wait(10)
            b = second.submit(approve, True)
            assert second_started.wait(10)
            release.set()
            assert a.result(timeout=15) is True
            assert b.result(timeout=15) is False
        with app.app_context():
            profile = Member.query.filter_by(band_id=band_id, linked_user_id=user_id).one()
            assert profile.biography == '' and profile.positions_json == '[]'
            security_after = db.session.execute(text("SELECT relrowsecurity,relforcerowsecurity,relacl::text "
                "FROM pg_class WHERE oid='public.member'::regclass")).one()
            assert security_after == security_before
            db.session.rollback()
            assert_postgres_startup_ready(db.engine)
    finally:
        release.set()
        with app.app_context():
            db.session.remove(); db.engine.dispose()
