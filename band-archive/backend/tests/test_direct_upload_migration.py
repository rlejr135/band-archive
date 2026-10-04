"""Portable DDL contracts for the v2 direct-upload reservation table."""

from io import StringIO
import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
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


def test_direct_upload_expiry_cleanup_status_migrates_on_sqlite_and_postgres(monkeypatch):
    session_revision = importlib.import_module('migrations.versions.20261002_09_direct_upload_session')
    expiry_revision = importlib.import_module('migrations.versions.20261003_10_direct_upload_expiry_cleanup')
    monkeypatch.setattr(session_revision.context, 'is_offline_mode', lambda: False)
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            # SQLite batch recreation reflects every referenced table.
            for table in ('band', 'band_membership', 'song', 'rehearsal', 'member', 'media', 'personal_log'):
                connection.exec_driver_sql(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
            _run_revision(connection, session_revision.upgrade)
            _run_revision(connection, expiry_revision.upgrade)
            checks = inspect(connection).get_check_constraints('direct_upload_session')
            assert any('expiring' in (check['sqltext'] or '') for check in checks)
            connection.exec_driver_sql("""
                INSERT INTO direct_upload_session (
                    id, band_id, created_by_membership_id, kind, object_key,
                    original_filename, content_type, declared_bytes, song_id,
                    status, capability_token_hash, created_at, expires_at
                ) VALUES (
                    'cleanup-in-progress', 1, 1, 'media', 'bands/1/media/cleanup.mp4',
                    'cleanup.mp4', 'video/mp4', 10, 1,
                    'expiring', 'capability', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """)
            with pytest.raises(RuntimeError, match='expiry cleanup is in progress'):
                _run_revision(connection, expiry_revision.downgrade)
            connection.exec_driver_sql(
                "DELETE FROM direct_upload_session WHERE id = 'cleanup-in-progress'"
            )
            _run_revision(connection, expiry_revision.downgrade)
            checks = inspect(connection).get_check_constraints('direct_upload_session')
            assert all('expiring' not in (check['sqltext'] or '') for check in checks)
            _run_revision(connection, session_revision.downgrade)
    finally:
        engine.dispose()

    upgrade_sql = _offline_postgres_sql(expiry_revision.upgrade)
    downgrade_sql = _offline_postgres_sql(expiry_revision.downgrade)
    assert 'DROP CONSTRAINT ck_direct_upload_session_status' in upgrade_sql
    assert 'expiring' in upgrade_sql
    assert 'DROP CONSTRAINT ck_direct_upload_session_status' in downgrade_sql


def test_direct_gallery_result_migrates_from_phase10_on_sqlite(monkeypatch):
    session_revision = importlib.import_module('migrations.versions.20261002_09_direct_upload_session')
    expiry_revision = importlib.import_module('migrations.versions.20261003_10_direct_upload_expiry_cleanup')
    gallery_revision = importlib.import_module('migrations.versions.20261004_11_direct_gallery_upload')
    monkeypatch.setattr(session_revision.context, 'is_offline_mode', lambda: False)
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            # The direct-session table carries FKs to all of these parents;
            # gallery_image is the only new parent required by revision 11.
            for table in ('band', 'band_membership', 'song', 'rehearsal', 'member', 'media', 'personal_log', 'gallery_image'):
                connection.exec_driver_sql(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
            _run_revision(connection, session_revision.upgrade)
            _run_revision(connection, expiry_revision.upgrade)
            _run_revision(connection, gallery_revision.upgrade)
            columns = {column['name'] for column in inspect(connection).get_columns('direct_upload_session')}
            assert 'result_gallery_image_id' in columns
            checks = inspect(connection).get_check_constraints('direct_upload_session')
            assert any('gallery' in (check['sqltext'] or '') for check in checks)
            _run_revision(connection, gallery_revision.downgrade)
            columns = {column['name'] for column in inspect(connection).get_columns('direct_upload_session')}
            assert 'result_gallery_image_id' not in columns
    finally:
        engine.dispose()

    upgrade_sql = _offline_postgres_sql(gallery_revision.upgrade)
    downgrade_sql = _offline_postgres_sql(gallery_revision.downgrade)
    assert 'ADD COLUMN result_gallery_image_id' in upgrade_sql
    assert 'FOREIGN KEY(result_gallery_image_id) REFERENCES gallery_image (id)' in upgrade_sql
    assert 'DROP CONSTRAINT ck_direct_upload_session_kind' in upgrade_sql
    assert "'gallery'" in upgrade_sql
    assert 'DROP COLUMN result_gallery_image_id' in downgrade_sql
