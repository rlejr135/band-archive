from flask_migrate import upgrade
from sqlalchemy import inspect, text
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from click.testing import CliRunner
import pytest

from extensions import db
from migration_bootstrap import (
    ALEMBIC_VERSION_TABLE,
    BASELINE_REVISION,
    MIGRATIONS_DIRECTORY,
    baseline_metadata,
    create_migration_app,
    migration_head_revision,
    schema_contract_differences,
    schema_differences,
    types_are_equivalent,
)
from migration_runner import cli
from app import create_app
from config import TestingConfig


def _database_url(database_path):
    return f"sqlite:///{database_path.as_posix()}"


def _migration_app(database_path):
    return create_migration_app(_database_url(database_path))


def _runner_command(database_path, command):
    return CliRunner().invoke(cli, ['--database-url', _database_url(database_path), command])


def _version(app):
    with app.app_context():
        with db.engine.connect() as connection:
            return connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()


def _dispose(app):
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _runtime_config(database_path, auth_enabled=False):
    return type('RuntimeConfig', (TestingConfig,), {
        'SQLALCHEMY_DATABASE_URI': _database_url(database_path),
        'SUPABASE_AUTH_ENABLED': auth_enabled,
        'SUPABASE_URL': 'https://project.supabase.co',
        'SUPABASE_PROJECT_REF': 'project',
        'SUPABASE_JWT_ISSUER': 'https://project.supabase.co/auth/v1',
        'SUPABASE_JWT_AUDIENCE': 'authenticated',
        'SUPABASE_JWKS_URL': 'https://project.supabase.co/auth/v1/.well-known/jwks.json',
        'SUPABASE_JWKS_CACHE_SECONDS': 60,
        'SUPABASE_HTTP_TIMEOUT_SECONDS': 1,
        'SUPABASE_JWT_LEEWAY_SECONDS': 0,
        'BOOTSTRAP_PLATFORM_ADMIN_EMAIL': 'admin@example.com',
    })


def test_fresh_database_upgrade_creates_current_baseline(tmp_path):
    database_path = tmp_path / 'fresh.db'
    result = _runner_command(database_path, 'upgrade')
    assert result.exit_code == 0, result.output

    app = _migration_app(database_path)
    try:
        with app.app_context():
            upgrade(directory=str(MIGRATIONS_DIRECTORY))
            tables = set(inspect(db.engine).get_table_names())
            assert set(db.metadata.tables).issubset(tables)
            assert ALEMBIC_VERSION_TABLE in tables
            with db.engine.connect() as connection:
                assert schema_differences(connection) == []
                assert schema_contract_differences(connection) == []
        with app.app_context():
            assert _version(app) == migration_head_revision()
    finally:
        _dispose(app)


def test_existing_current_schema_can_stamp_then_upgrade_idempotently(tmp_path):
    database_path = tmp_path / 'existing.db'
    app = _migration_app(database_path)
    try:
        with app.app_context():
            baseline_metadata().create_all(db.engine)

        first = _runner_command(database_path, 'stamp-existing')
        assert first.exit_code == 0, first.output
        assert 'Stamped existing current schema' in first.output
        assert _version(app) == BASELINE_REVISION

        second = _runner_command(database_path, 'stamp-existing')
        assert second.exit_code == 0, second.output
        assert 'already stamped' in second.output
        assert _version(app) == BASELINE_REVISION

        upgraded = _runner_command(database_path, 'upgrade')
        assert upgraded.exit_code == 0, upgraded.output
        with app.app_context():
            assert _version(app) == migration_head_revision()
    finally:
        _dispose(app)


def test_revision_two_exact_snapshot_allows_only_compatible_phase_three_upgrade(tmp_path):
    """Revision 02 needs its own snapshot, rather than comparing against head."""
    database_path = tmp_path / 'revision-two.db'
    app = _migration_app(database_path)
    try:
        with app.app_context():
            upgrade(revision='20260914_02', directory=str(MIGRATIONS_DIRECTORY))
            assert _version(app) == '20260914_02'
        result = _runner_command(database_path, 'upgrade')
        assert result.exit_code == 0, result.output
        with app.app_context():
            assert _version(app) == migration_head_revision()
    finally:
        _dispose(app)


def test_stamp_refuses_partial_or_mismatched_existing_schema(tmp_path):
    database_path = tmp_path / 'mismatch.db'
    app = _migration_app(database_path)
    try:
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text('CREATE TABLE song (id INTEGER PRIMARY KEY)'))

        before = database_path.read_bytes()
        result = _runner_command(database_path, 'upgrade')
        assert result.exit_code != 0
        assert 'application tables but no Alembic revision' in result.output
        assert database_path.read_bytes() == before

        with app.app_context():
            assert ALEMBIC_VERSION_TABLE not in inspect(db.engine).get_table_names()
    finally:
        _dispose(app)


def test_stamp_refuses_empty_database_instead_of_marking_it_current(tmp_path):
    database_path = tmp_path / 'empty.db'
    app = _migration_app(database_path)
    try:
        result = _runner_command(database_path, 'stamp-existing')
        assert result.exit_code != 0
        assert 'migration_runner.py --database-url ... upgrade' in result.output
    finally:
        _dispose(app)


def test_upgrade_refuses_empty_or_invalid_alembic_version_table(tmp_path):
    database_path = tmp_path / 'invalid-version.db'
    app = _migration_app(database_path)
    try:
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
        result = _runner_command(database_path, 'upgrade')
        assert result.exit_code != 0
        assert 'alembic_version table but no application schema' in result.output
        with app.app_context():
            assert inspect(db.engine).get_table_names() == [ALEMBIC_VERSION_TABLE]
    finally:
        _dispose(app)


class _TamperedInspector:
    """Expose real SQLite metadata except for selected constraint defects."""

    def __init__(self, inspector):
        self._inspector = inspector

    def __getattr__(self, name):
        return getattr(self._inspector, name)

    def get_columns(self, table_name):
        columns = [dict(column) for column in self._inspector.get_columns(table_name)]
        if table_name == 'song':
            next(column for column in columns if column['name'] == 'upvote_count')['default'] = '1'
        return columns

    def get_foreign_keys(self, table_name):
        if table_name == 'song_vote':
            return []
        return self._inspector.get_foreign_keys(table_name)

    def get_unique_constraints(self, table_name):
        if table_name == 'song_vote':
            return []
        return self._inspector.get_unique_constraints(table_name)

    def get_check_constraints(self, table_name):
        if table_name == 'song_vote':
            return []
        return self._inspector.get_check_constraints(table_name)

    def get_indexes(self, table_name):
        if table_name == 'song_vote':
            return []
        return self._inspector.get_indexes(table_name)


def test_structural_verifier_rejects_constraint_index_and_default_defects(tmp_path):
    app = _migration_app(tmp_path / 'structural-defects.db')
    try:
        with app.app_context():
            db.create_all()
            with db.engine.connect() as connection:
                differences = schema_contract_differences(
                    connection, inspector_override=_TamperedInspector(inspect(connection))
                )
        joined = '\n'.join(differences)
        assert 'song.upvote_count column' in joined
        assert 'song_vote.foreign_keys' in joined
        assert 'song_vote.unique_constraints' in joined
        assert 'song_vote.check_constraints' in joined
        assert 'song_vote.indexes' in joined
    finally:
        _dispose(app)


def test_structural_verifier_rejects_changed_vote_check_expression(tmp_path):
    app = _migration_app(tmp_path / 'changed-check.db')
    try:
        class _ChangedCheckInspector(_TamperedInspector):
            def get_check_constraints(self, table_name):
                constraints = [dict(item) for item in self._inspector.get_check_constraints(table_name)]
                if table_name == 'song_vote':
                    constraints[0]['sqltext'] = 'value IN (-1, 0, 1)'
                return constraints

        with app.app_context():
            db.create_all()
            with db.engine.connect() as connection:
                differences = schema_contract_differences(
                    connection, inspector_override=_ChangedCheckInspector(inspect(connection))
                )
        assert any('song_vote.check_constraints' in difference for difference in differences)
    finally:
        _dispose(app)


def test_postgres_float_and_double_precision_are_semantically_equivalent():
    dialect = postgresql.dialect()
    assert types_are_equivalent(sa.Float(), postgresql.DOUBLE_PRECISION(), dialect)
    assert not types_are_equivalent(sa.Float(), postgresql.REAL(), dialect)
    assert not types_are_equivalent(sa.Float(), sa.Integer(), dialect)
    assert not types_are_equivalent(sa.Integer(), sa.String(20), dialect)


def test_runtime_boot_does_not_create_auth_tables_before_runner_upgrade(tmp_path):
    database_path = tmp_path / 'stamped-baseline.db'
    migration_app = _migration_app(database_path)
    try:
        with migration_app.app_context():
            baseline_metadata().create_all(db.engine)
        assert _runner_command(database_path, 'stamp-existing').exit_code == 0
    finally:
        _dispose(migration_app)

    before_runtime_boot = database_path.read_bytes()
    runtime = create_app(_runtime_config(database_path), start_worker=False)
    try:
        with runtime.app_context():
            tables = set(inspect(db.engine).get_table_names())
            assert 'app_user' not in tables
            assert 'auth_bootstrap_state' not in tables
            assert 'band' not in tables
            assert 'band_membership' not in tables
            assert _version(runtime) == BASELINE_REVISION
    finally:
        _dispose(runtime)
    assert database_path.read_bytes() == before_runtime_boot

    upgraded = _runner_command(database_path, 'upgrade')
    assert upgraded.exit_code == 0, upgraded.output
    repeated = _runner_command(database_path, 'upgrade')
    assert repeated.exit_code == 0, repeated.output
    runtime_after_upgrade = create_app(_runtime_config(database_path), start_worker=False)
    try:
        with runtime_after_upgrade.app_context():
            assert {'app_user', 'auth_bootstrap_state'}.issubset(inspect(db.engine).get_table_names())
            assert _version(runtime_after_upgrade) == migration_head_revision()
    finally:
        _dispose(runtime_after_upgrade)


def test_enabled_auth_refuses_startup_until_runner_upgrade(tmp_path):
    database_path = tmp_path / 'auth-not-upgraded.db'
    with pytest.raises(RuntimeError, match='isolated Alembic upgrade'):
        create_app(_runtime_config(database_path, auth_enabled=True), start_worker=False)


def test_stamped_revision_preflight_rejects_unexpected_or_tampered_auth_schema(tmp_path):
    database_path = tmp_path / 'revision-contract.db'
    migration_app = _migration_app(database_path)
    try:
        with migration_app.app_context():
            baseline_metadata().create_all(db.engine)
        assert _runner_command(database_path, 'stamp-existing').exit_code == 0
    finally:
        _dispose(migration_app)

    # A v01 database must not contain tables from v02 before its upgrade.
    unexpected = _migration_app(database_path)
    try:
        with unexpected.app_context():
            db.metadata.tables['app_user'].create(db.engine)
        result = _runner_command(database_path, 'upgrade')
        assert result.exit_code != 0
        assert 'does not match the checked-in baseline' in result.output
    finally:
        _dispose(unexpected)

    # Recreate a valid head, then prove missing v02 constraints are checked
    # before a repeated upgrade can claim it is safe.
    clean_path = tmp_path / 'head-contract.db'
    assert _runner_command(clean_path, 'upgrade').exit_code == 0
    head_app = _migration_app(clean_path)
    try:
        with head_app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text('PRAGMA foreign_keys=OFF'))
                connection.execute(text('ALTER TABLE auth_bootstrap_state RENAME TO broken_auth_bootstrap_state'))
                connection.execute(text(
                    'CREATE TABLE auth_bootstrap_state ('
                    'key VARCHAR(64) NOT NULL, '
                    'claimed_by_user_id INTEGER NOT NULL, '
                    'claimed_at DATETIME NOT NULL, '
                    'PRIMARY KEY (key))'
                ))
                connection.execute(text(
                    'INSERT INTO auth_bootstrap_state (key, claimed_by_user_id, claimed_at) '
                    'SELECT key, claimed_by_user_id, claimed_at FROM broken_auth_bootstrap_state'
                ))
                connection.execute(text('DROP TABLE broken_auth_bootstrap_state'))
        result = _runner_command(clean_path, 'upgrade')
        assert result.exit_code != 0
        assert 'auth_bootstrap_state.foreign_keys' in result.output
    finally:
        _dispose(head_app)
