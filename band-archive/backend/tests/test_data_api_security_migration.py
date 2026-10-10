"""Private PostgreSQL contract for the Flask-only Data API boundary."""

from __future__ import annotations

import importlib
import socket
import subprocess
import time
from pathlib import Path

from alembic import command
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade
import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text

from auth_service import VerifiedIdentity, synchronize_user
from extensions import db
from migration_bootstrap import (
    DATA_API_MANAGED_TABLES,
    MIGRATIONS_DIRECTORY,
    PHASE12_REVISION,
    create_migration_app,
    metadata_for_recorded_revision,
    phase11_metadata,
    phase12_metadata,
    schema_contract_differences,
)
from tenant_backfill import apply as apply_tenant_backfill


PG_BIN = Path(__file__).parents[1] / 'tools' / '.state' / 'postgresql117' / 'pgsql' / 'bin'
SECURITY_REVISION = '20261010_13'


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def private_pg17(tmp_path):
    assert (PG_BIN / 'initdb.exe').is_file(), 'private PostgreSQL fixture is required for this integration test'
    port = _free_port()
    data, log = tmp_path / 'pgdata', tmp_path / 'postgres.log'
    subprocess.run(
        [str(PG_BIN / 'initdb.exe'), '--no-locale', '--encoding=UTF8', '--username=postgres',
         '--auth=trust', '--pgdata', str(data)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    with log.open('ab') as stream:
        process = subprocess.Popen(
            [str(PG_BIN / 'postgres.exe'), '-D', str(data), '-h', '127.0.0.1', '-p', str(port)],
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('private PostgreSQL exited during startup')
                try:
                    # A listening socket is observable before PostgreSQL has
                    # completed startup.  Prove it accepts SQL too, otherwise
                    # the next migration connection can nondeterministically
                    # receive "the database system is starting up".
                    readiness_url = f'postgresql+psycopg://postgres@127.0.0.1:{port}/postgres'
                    with create_engine(readiness_url).connect() as connection:
                        connection.execute(text('SELECT 1'))
                    break
                except (OSError, sa.exc.OperationalError):
                    time.sleep(0.1)
            else:
                raise RuntimeError('private PostgreSQL did not start')
            yield f'postgresql+psycopg://postgres@127.0.0.1:{port}/postgres'
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)


def _upgrade_to_revision_12(url):
    app = create_migration_app(url)
    try:
        with app.app_context():
            upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                apply_tenant_backfill(connection)
            upgrade(revision=PHASE12_REVISION, directory=str(MIGRATIONS_DIRECTORY))
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def _table_snapshot(connection):
    inspector = inspect(connection)
    return {
        table: {
            'columns': tuple((item['name'], str(item['type']), bool(item['nullable']))
                             for item in inspector.get_columns(table)),
            'foreign_key_count': len(inspector.get_foreign_keys(table)),
            'row_count': connection.execute(text(f'SELECT count(*) FROM public."{table}"')).scalar_one(),
        }
        for table in DATA_API_MANAGED_TABLES
    }


def _create_client_roles_and_exposure(connection):
    connection.execute(text("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
        END $$
    """))
    for table in DATA_API_MANAGED_TABLES:
        connection.execute(text(f'GRANT ALL PRIVILEGES ON TABLE public."{table}" TO PUBLIC, anon, authenticated'))
    connection.execute(text('GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO PUBLIC, anon, authenticated'))
    connection.execute(text('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO PUBLIC, anon, authenticated'))
    connection.execute(text('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO PUBLIC, anon, authenticated'))


def _assert_role_denied(engine, role, statement):
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        connection.execute(text(f'SET ROLE "{role}"'))
        try:
            with pytest.raises(sa.exc.DBAPIError):
                connection.execute(text(statement))
        finally:
            connection.execute(text('RESET ROLE'))


def test_phase12_snapshot_is_built_from_phase11_plus_legacy_claim_table():
    phase11 = phase11_metadata()
    phase12 = phase12_metadata()
    assert 'legacy_band_claim_token' not in phase11.tables
    assert 'legacy_band_claim_token' in phase12.tables
    assert phase12.tables['legacy_band_claim_token'] is not db.metadata.tables['legacy_band_claim_token']
    assert metadata_for_recorded_revision(PHASE12_REVISION) is not db.metadata


def test_sqlite_revision_is_version_only_and_downgrade_is_safe_noop(tmp_path):
    app = create_migration_app(f'sqlite:///{(tmp_path / "security.db").as_posix()}')
    try:
        with app.app_context():
            upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                apply_tenant_backfill(connection)
            upgrade(revision=SECURITY_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            assert schema_contract_differences(db.session.connection()) == []
            downgrade(revision=PHASE12_REVISION, directory=str(MIGRATIONS_DIRECTORY))
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_postgres_migration_blocks_client_roles_preserves_owner_and_future_defaults(private_pg17):
    _upgrade_to_revision_12(private_pg17)
    engine = create_engine(private_pg17)
    try:
        with engine.begin() as connection:
            before = _table_snapshot(connection)
            _create_client_roles_and_exposure(connection)

        app = create_migration_app(private_pg17)
        try:
            with app.app_context():
                upgrade(revision=SECURITY_REVISION, directory=str(MIGRATIONS_DIRECTORY))
                assert schema_contract_differences(db.session.connection()) == []
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

        with engine.begin() as connection:
            assert _table_snapshot(connection) == before
            connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('owner-security-probe')"))
            connection.execute(text("UPDATE alembic_version SET version_num = 'owner-security-probe-updated' "
                                    "WHERE version_num = 'owner-security-probe'"))
            connection.execute(text("DELETE FROM alembic_version WHERE version_num = 'owner-security-probe-updated'"))
            connection.execute(text('CREATE TABLE public.data_api_default_acl_probe (id SERIAL PRIMARY KEY)'))

        for role in ('anon', 'authenticated'):
            _assert_role_denied(engine, role, 'SELECT id FROM public.app_user LIMIT 1')
            _assert_role_denied(engine, role, "INSERT INTO public.app_user "
                                "(supabase_user_id, email, status, email_verified, is_platform_admin, created_at, updated_at) "
                                "VALUES ('role-probe', 'role-probe@example.test', 'active', true, false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
            _assert_role_denied(engine, role, "UPDATE public.app_user SET status = 'active' WHERE false")
            _assert_role_denied(engine, role, 'DELETE FROM public.app_user WHERE false')
            _assert_role_denied(engine, role, 'SELECT id FROM public.data_api_default_acl_probe LIMIT 1')
            _assert_role_denied(engine, role, "SELECT nextval(pg_get_serial_sequence('public.data_api_default_acl_probe', 'id'))")
    finally:
        engine.dispose()


def test_postgres_global_default_acl_blocks_before_any_security_ddl(private_pg17):
    _upgrade_to_revision_12(private_pg17)
    engine = create_engine(private_pg17)
    try:
        with engine.begin() as connection:
            _create_client_roles_and_exposure(connection)
            connection.execute(text('ALTER DEFAULT PRIVILEGES GRANT ALL PRIVILEGES ON TABLES TO anon'))
            before_table_state = connection.execute(text("""
                SELECT cls.relrowsecurity, cls.relforcerowsecurity,
                       COALESCE(cls.relacl::text, '')
                FROM pg_class AS cls
                JOIN pg_namespace AS namespace ON namespace.oid = cls.relnamespace
                WHERE namespace.nspname = 'public' AND cls.relname = 'app_user'
            """)).one()
            before_version = tuple(connection.execute(text(
                'SELECT version_num FROM alembic_version ORDER BY version_num'
            )).scalars())
        app = create_migration_app(private_pg17)
        try:
            with app.app_context():
                # Invoke Alembic directly for this negative assertion so the
                # migration's exact fail-closed reason is observable instead
                # of Flask-Migrate's CLI-style SystemExit(1) wrapper.
                migrate = app.extensions['migrate'].migrate
                config = migrate.get_config(str(MIGRATIONS_DIRECTORY))
                with pytest.raises(RuntimeError, match='unsafe global default privileges'):
                    command.upgrade(config, SECURITY_REVISION)
                after_table_state = db.session.connection().execute(text("""
                    SELECT cls.relrowsecurity, cls.relforcerowsecurity,
                           COALESCE(cls.relacl::text, '')
                    FROM pg_class AS cls
                    JOIN pg_namespace AS namespace ON namespace.oid = cls.relnamespace
                    WHERE namespace.nspname = 'public' AND cls.relname = 'app_user'
                """)).one()
                after_version = tuple(db.session.connection().execute(text(
                    'SELECT version_num FROM alembic_version ORDER BY version_num'
                )).scalars())
                assert after_table_state == before_table_state
                assert after_version == before_version == (PHASE12_REVISION,)
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
    finally:
        engine.dispose()
