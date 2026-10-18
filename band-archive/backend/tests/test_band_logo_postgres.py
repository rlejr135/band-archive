"""Owned-localhost PostgreSQL tests for the real revision-18-to-19 boundary."""
import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask_migrate import upgrade
from sqlalchemy import create_engine, text

from extensions import db
from migration_bootstrap import MIGRATIONS_DIRECTORY, create_migration_app
from tenant_backfill import apply as apply_tenant_backfill

_fixture_spec = importlib.util.spec_from_file_location(
    'band_logo_owned_pg_fixture', Path(__file__).with_name('test_preserve_identity_refresh_postgres.py'))
_fixture = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_fixture)
private_pg17 = _fixture.private_pg17
pytestmark = [pytest.mark.postgres_integration,
              pytest.mark.skipif(os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
                                 reason='explicitly provisioned owned localhost PG17 only')]


def _upgrade_to_18(url):
    app = create_migration_app(url)
    try:
        with app.app_context():
            upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                apply_tenant_backfill(connection)
            upgrade(revision='20261017_18', directory=str(MIGRATIONS_DIRECTORY))
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def _upgrade_to_head(url):
    app = create_migration_app(url)
    try:
        with app.app_context():
            upgrade(revision='head', directory=str(MIGRATIONS_DIRECTORY))
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def _ensure_browser_roles(url):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            for role in ('anon', 'authenticated'):
                if not connection.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:role'),
                                          {'role': role}).first():
                    connection.exec_driver_sql(f'CREATE ROLE {role} NOLOGIN')
    finally:
        engine.dispose()


def test_owned_postgres_18_to_19_upgrade_preserves_rows_and_hardens_logo_table(private_pg17):
    _upgrade_to_18(private_pg17)
    _ensure_browser_roles(private_pg17)
    app = create_migration_app(private_pg17)
    try:
        with app.app_context():
            now = datetime.now(timezone.utc)
            band_id = db.session.execute(text("""
                INSERT INTO band(name,slug,default_visibility,member_roster_public,storage_quota_bytes,
                                 is_legacy_import,created_at,updated_at)
                VALUES ('Logo migration fixture','logo-migration-fixture','PUBLIC',true,21474836480,
                        false,:now,:now) RETURNING id
            """), {'now': now}).scalar_one()
            image_id = db.session.execute(text("""
                INSERT INTO gallery_image(band_id,visibility,filename,object_key,is_featured,created_at)
                VALUES (:band,'PUBLIC','preserved.jpg','bands/logo-migration/preserved.jpg',false,:now)
                RETURNING id
            """), {'band': band_id, 'now': now}).scalar_one()
            db.session.commit()
            before = db.session.execute(text(
                'SELECT id,band_id,filename,object_key FROM gallery_image WHERE id=:id'),
                {'id': image_id}).one()
            db.session.remove()
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

    _upgrade_to_head(private_pg17)
    check_app = create_migration_app(private_pg17)
    try:
        with check_app.app_context():
            after = db.session.execute(text('SELECT id,band_id,filename,object_key FROM gallery_image WHERE id=:id'),
                                       {'id': image_id}).one()
            assert tuple(after) == tuple(before)
            assert db.session.execute(text('SELECT count(*) FROM band_logo')).scalar_one() == 0
            assert db.session.execute(text('SELECT count(*) FROM gallery_image WHERE band_id=:id'),
                                      {'id': band_id}).scalar_one() == 1
            assert db.session.execute(text("""
                SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.band_logo'::regclass
            """)).one() == (True, False)
            assert db.session.execute(text("""
                SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname='band_logo'
            """)).scalar_one() == 0
            assert db.session.execute(text("""
                SELECT has_table_privilege('anon','public.band_logo','SELECT')
                    OR has_table_privilege('authenticated','public.band_logo','SELECT')
            """)).scalar_one() is False
            db.session.rollback()
    finally:
        with check_app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_owned_postgres_18_to_19_migration_refuses_security_drift_before_ddl(private_pg17, capsys):
    _upgrade_to_18(private_pg17)
    engine = create_engine(private_pg17)
    with engine.begin() as connection:
        for role in ('anon', 'authenticated', 'logo_test_inherited'):
            if not connection.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:role'),
                                      {'role': role}).first():
                connection.exec_driver_sql(f'CREATE ROLE {role} NOLOGIN')

    def mutate(sql):
        with engine.begin() as connection:
            connection.exec_driver_sql(sql)

    def get_sequence():
        with engine.connect() as connection:
            return connection.execute(text(
                "SELECT pg_get_serial_sequence('public.gallery_image','id')")).scalar_one()

    def no_partial_ddl():
        with engine.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('public.band_logo') IS NULL")).scalar_one()
            assert connection.execute(text(
                "SELECT to_regclass('public.uq_gallery_image_band_id') IS NULL")).scalar_one()

    # The checks run sequentially against one owned, temporary revision-18
    # cluster; each defect is committed before the migrator checks it.
    def rejected(expected_message, capsys):
        migration_app = create_migration_app(private_pg17)
        try:
            with pytest.raises(SystemExit) as failure:
                with migration_app.app_context():
                    upgrade(revision='head', directory=str(MIGRATIONS_DIRECTORY))
            assert failure.value.code == 1
            captured = capsys.readouterr()
            assert expected_message in captured.out + captured.err
            no_partial_ddl()
        finally:
            with migration_app.app_context():
                db.session.remove()
                db.engine.dispose()

    mutate('CREATE POLICY logo_review_policy ON public.band USING (true)')
    rejected('must not have browser RLS policies', capsys)
    mutate('DROP POLICY logo_review_policy ON public.band')

    mutate('GRANT SELECT ON TABLE public.band TO PUBLIC')
    rejected('browser role retains application table privileges', capsys)
    mutate('REVOKE SELECT ON TABLE public.band FROM PUBLIC')

    mutate('GRANT logo_test_inherited TO anon')
    mutate('GRANT SELECT ON TABLE public.band TO logo_test_inherited')
    rejected('browser role retains application table privileges', capsys)
    mutate('REVOKE SELECT ON TABLE public.band FROM logo_test_inherited')
    mutate('REVOKE logo_test_inherited FROM anon')

    sequence = get_sequence()
    assert sequence
    mutate(f'GRANT USAGE ON SEQUENCE {sequence} TO PUBLIC')
    rejected('browser role retains application sequence privileges', capsys)
    mutate(f'REVOKE USAGE ON SEQUENCE {sequence} FROM PUBLIC')

    mutate('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO PUBLIC')
    rejected('Unsafe default table/sequence privileges', capsys)
    mutate('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM PUBLIC')
    engine.dispose()
