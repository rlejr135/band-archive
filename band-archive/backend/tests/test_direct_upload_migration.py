"""Portable DDL contracts for the v2 direct-upload reservation table."""

from io import StringIO
import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


def _run_revision(connection, operation):
    context = MigrationContext.configure(connection)
    operations = Operations(context)
    operations._install_proxy()
    try:
        operation()
    finally:
        operations._remove_proxy()


def _offline_postgres_sql(operation):
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output},
    )
    operations = Operations(context)
    operations._install_proxy()
    try:
        operation()
    finally:
        operations._remove_proxy()
    return output.getvalue()


def test_direct_upload_session_postgres_offline_ddl_is_complete(monkeypatch):
    revision = importlib.import_module('migrations.versions.20261002_09_direct_upload_session')
    monkeypatch.setattr(revision.context, 'is_offline_mode', lambda: True)
    upgrade_sql = _offline_postgres_sql(revision.upgrade)
    downgrade_sql = _offline_postgres_sql(revision.downgrade)

    assert 'CREATE TABLE direct_upload_session' in upgrade_sql
    assert 'ck_direct_upload_session_target' in upgrade_sql
    assert 'FOREIGN KEY(band_id) REFERENCES band (id)' in upgrade_sql
    assert 'CREATE INDEX ix_direct_upload_session_band_id' in upgrade_sql
    assert 'DROP TABLE direct_upload_session' in downgrade_sql


def test_direct_upload_session_sqlite_roundtrip(monkeypatch):
    revision = importlib.import_module('migrations.versions.20261002_09_direct_upload_session')
    monkeypatch.setattr(revision.context, 'is_offline_mode', lambda: False)
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            _run_revision(connection, revision.upgrade)
            table = inspect(connection).get_columns('direct_upload_session')
            assert {column['name'] for column in table} >= {
                'band_id', 'created_by_membership_id', 'object_key', 'declared_bytes',
                'capability_token_hash', 'media_id', 'personal_log_id',
            }
            _run_revision(connection, revision.downgrade)
            assert 'direct_upload_session' not in inspect(connection).get_table_names()
    finally:
        engine.dispose()
