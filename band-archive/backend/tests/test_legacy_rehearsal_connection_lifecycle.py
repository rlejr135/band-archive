"""Offline SQLite connections close deterministically without garbage collection.

Retaining strong references deliberately prevents refcount/GC timing from
masking a leaked handle. All databases below are owned temporary fixtures.
"""

from contextlib import closing
import sqlite3

import pytest

import tools.legacy_rehearsal.rehearsal as rehearsal
from tests.test_legacy_rehearsal import _exact_fly_legacy_fixture


@pytest.fixture
def retained_connections(monkeypatch):
    original = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(rehearsal.sqlite3, 'connect', connect)
    try:
        yield connections
    finally:
        # Also clean up after a failing assertion, rather than leaving handles
        # open and obscuring the lifecycle regression with Windows unlink errors.
        for connection in connections:
            connection.close()


def _assert_closed(connections):
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')


@pytest.mark.parametrize('reader', ['preflight', 'triggers', 'revision', 'digests'])
def test_read_helpers_close_retained_connections_on_success(tmp_path, retained_connections, reader):
    path = tmp_path / 'source.sqlite'
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('CREATE TABLE alembic_version (version_num TEXT)')
        connection.execute("INSERT INTO alembic_version VALUES ('20260914_01')")
    if reader == 'preflight':
        assert rehearsal.preflight_sqlite_snapshot(str(path)) == path
    elif reader == 'triggers':
        rehearsal._assert_exact_sqlite_trigger_contract(path, '20260914_01')
    elif reader == 'revision':
        assert rehearsal._assert_supported_source_schema(path) == ('20260914_01', None)
    else:
        contract = {'tables': {'alembic_version': {'columns': [{'name': 'version_num', 'pk': 1}]}}}
        assert rehearsal._mapped_sqlite_digests(path, contract)['alembic_version']['count'] == 1
    _assert_closed(retained_connections)


def test_digest_reader_closes_retained_connection_on_sql_error(tmp_path, retained_connections):
    path = tmp_path / 'source.sqlite'
    with closing(sqlite3.connect(path)):
        pass
    contract = {'tables': {'missing_table': {'columns': [{'name': 'id', 'pk': 1}]}}}
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        rehearsal._mapped_sqlite_digests(path, contract)
    _assert_closed(retained_connections)


@pytest.mark.parametrize('failure', [None, 'copy_sql', 'source_connect'])
def test_normalizer_closes_both_retained_connections_on_success_and_failure(
        tmp_path, monkeypatch, failure):
    snapshot, working = tmp_path / 'snapshot.sqlite', tmp_path / 'working.sqlite'
    _exact_fly_legacy_fixture(snapshot)
    fingerprint = rehearsal._file_fingerprint(snapshot)
    # Warm the baseline before tracking only the normalizer's database handles.
    rehearsal._baseline_sqlite_contract()
    original = sqlite3.connect
    connections = []
    source_connections = []
    source_uri = f'file:{snapshot.as_posix()}?mode=ro'

    def connect(database, *args, **kwargs):
        if database == source_uri:
            source_connections.append(True)
            # Values/digest readers run before opening the target. Fail only
            # when the target has been opened and the copy source is requested.
            target_open = any(name == working for name, _ in connections)
            if failure == 'source_connect' and target_open:
                raise sqlite3.OperationalError('injected source connect failure')
        connection = original(database, *args, **kwargs)
        connections.append((database, connection))
        if failure == 'copy_sql' and database == working:
            # Deny a real row INSERT only on the copy destination, after its
            # explicit BEGIN, so the normalizer's sqlite-error rollback runs.
            connection.set_authorizer(lambda action, table, *unused: (
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT
                and table not in {'sqlite_master', 'sqlite_temp_master'} else sqlite3.SQLITE_OK))
        return connection

    monkeypatch.setattr(rehearsal.sqlite3, 'connect', connect)
    try:
        if failure == 'copy_sql':
            with pytest.raises(rehearsal.RehearsalError, match='copy_contract'):
                rehearsal._normalize_production_legacy_copy(snapshot, working, fingerprint, tmp_path)
        elif failure == 'source_connect':
            with pytest.raises(sqlite3.OperationalError, match='injected source connect failure'):
                rehearsal._normalize_production_legacy_copy(snapshot, working, fingerprint, tmp_path)
        else:
            result = rehearsal._normalize_production_legacy_copy(snapshot, working, fingerprint, tmp_path)
            assert result['variant'] == 'fly_production_legacy_20260919'
        assert source_connections
        assert any(name == working for name, _ in connections)
        _assert_closed([connection for _, connection in connections])
    finally:
        for _, connection in connections:
            connection.close()
