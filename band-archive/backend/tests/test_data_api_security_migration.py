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
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine

from auth_service import VerifiedIdentity, synchronize_user
from extensions import db
from migration_bootstrap import (
    DATA_API_MANAGED_TABLES_PHASE13 as DATA_API_MANAGED_TABLES,
    DATA_API_MANAGED_TABLES as CURRENT_DATA_API_MANAGED_TABLES,
    MIGRATIONS_DIRECTORY,
    PHASE12_REVISION,
    PHASE13_REVISION,
    PHASE15_REVISION,
    create_migration_app,
    migration_head_revision,
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
            assert schema_contract_differences(
                db.session.connection(), metadata=metadata_for_recorded_revision(SECURITY_REVISION),
            ) == []
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
                assert schema_contract_differences(
                    db.session.connection(), metadata=metadata_for_recorded_revision(SECURITY_REVISION),
                ) == []
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


def test_postgres_email_outbox_revision_is_private_and_exact_head_is_required(private_pg17):
    _upgrade_to_revision_12(private_pg17)
    app = create_migration_app(private_pg17)
    engine = create_engine(private_pg17)
    try:
        with app.app_context():
            upgrade(revision=SECURITY_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                connection.execute(text("""
                    DO $$ BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
                        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
                    END $$
                """))
            from startup_guard import assert_postgres_startup_ready
            with pytest.raises(RuntimeError, match='PostgreSQL startup validation failed'):
                assert_postgres_startup_ready(engine)
            upgrade(directory=str(MIGRATIONS_DIRECTORY))
            assert schema_contract_differences(db.session.connection()) == []
            assert_postgres_startup_ready(engine)

        with engine.begin() as connection:
            table_names = tuple(connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )).scalars())
            assert table_names == CURRENT_DATA_API_MANAGED_TABLES
            state = connection.execute(text("""
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class WHERE oid='public.band_request_email_outbox'::regclass
            """)).one()
            assert state == (True, False)
            connection.execute(text(
                "INSERT INTO public.band_request_email_outbox "
                "(decision, next_attempt_at, created_at, updated_at) "
                "VALUES ('approved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
            connection.execute(text(
                "DELETE FROM public.band_request_email_outbox WHERE decision='approved'"
            ))

        for role in ('anon', 'authenticated'):
            _assert_role_denied(engine, role, 'SELECT id FROM public.band_request_email_outbox LIMIT 1')
            _assert_role_denied(
                engine, role,
                "INSERT INTO public.band_request_email_outbox "
                "(decision, next_attempt_at, created_at, updated_at) "
                "VALUES ('approved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            )
            _assert_role_denied(
                engine, role,
                "SELECT nextval(pg_get_serial_sequence('public.band_request_email_outbox', 'id'))",
            )

        # Prove the downgrade's table lock is acquired before its empty-table
        # check: a second connection cannot insert during the vulnerable gap.
        concurrent_insert_blocked = []
        def pause_at_history_count(connection, cursor, statement, parameters, context, executemany):
            if statement.strip().casefold() == 'select count(*) from band_request_email_outbox':
                with engine.connect() as concurrent:
                    transaction = concurrent.begin()
                    try:
                        concurrent.exec_driver_sql("SET LOCAL lock_timeout = '250ms'")
                        try:
                            concurrent.execute(text(
                                "INSERT INTO public.band_request_email_outbox "
                                "(decision, next_attempt_at, created_at, updated_at) "
                                "VALUES ('approved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                            ))
                        except sa.exc.DBAPIError:
                            concurrent_insert_blocked.append(True)
                        else:
                            concurrent_insert_blocked.append(False)
                    finally:
                        transaction.rollback()

        event.listen(Engine, 'before_cursor_execute', pause_at_history_count)
        try:
            with app.app_context():
                migrate = app.extensions['migrate'].migrate
                command.downgrade(migrate.get_config(str(MIGRATIONS_DIRECTORY)), PHASE13_REVISION)
        finally:
            event.remove(Engine, 'before_cursor_execute', pause_at_history_count)
        assert concurrent_insert_blocked == [True]

        # A populated history blocks downgrade and survives the failed Alembic
        # transaction; a rollback never silently loses an event.
        with app.app_context():
            upgrade(directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO public.band_request_email_outbox "
                    "(decision, next_attempt_at, created_at, updated_at) "
                    "VALUES ('rejected', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ))
            migrate = app.extensions['migrate'].migrate
            with pytest.raises(RuntimeError, match='Refusing to drop a populated email outbox'):
                command.downgrade(migrate.get_config(str(MIGRATIONS_DIRECTORY)), PHASE13_REVISION)
            with db.engine.connect() as connection:
                assert connection.execute(text(
                    'SELECT count(*) FROM public.band_request_email_outbox'
                )).scalar_one() == 1
                assert connection.execute(text(
                    'SELECT version_num FROM public.alembic_version'
                )).scalar_one() == migration_head_revision()
                assert connection.execute(text(
                    "SELECT to_regclass('public.r2_deletion_task') IS NOT NULL"
                )).scalar_one() is True
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        engine.dispose()
