from flask_migrate import upgrade
from sqlalchemy import inspect, text
from click.testing import CliRunner

from extensions import db
from migration_bootstrap import (
    ALEMBIC_VERSION_TABLE,
    BASELINE_REVISION,
    MIGRATIONS_DIRECTORY,
    create_migration_app,
    schema_differences,
)
from migration_runner import cli


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
        assert _version(app) == BASELINE_REVISION
    finally:
        _dispose(app)


def test_existing_current_schema_can_stamp_then_upgrade_idempotently(tmp_path):
    database_path = tmp_path / 'existing.db'
    app = _migration_app(database_path)
    try:
        with app.app_context():
            db.create_all()

        first = _runner_command(database_path, 'stamp-existing')
        assert first.exit_code == 0, first.output
        assert 'Stamped existing current schema' in first.output
        assert _version(app) == BASELINE_REVISION

        upgraded = _runner_command(database_path, 'upgrade')
        assert upgraded.exit_code == 0, upgraded.output

        second = _runner_command(database_path, 'stamp-existing')
        assert second.exit_code == 0, second.output
        assert 'already stamped' in second.output
        assert _version(app) == BASELINE_REVISION
    finally:
        _dispose(app)


def test_stamp_refuses_partial_or_mismatched_existing_schema(tmp_path):
    database_path = tmp_path / 'mismatch.db'
    app = _migration_app(database_path)
    try:
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text('CREATE TABLE song (id INTEGER PRIMARY KEY)'))

        result = _runner_command(database_path, 'stamp-existing')
        assert result.exit_code != 0
        assert 'does not match the checked-in baseline' in result.output

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
        assert 'Run `flask db upgrade` instead' in result.output
    finally:
        _dispose(app)
