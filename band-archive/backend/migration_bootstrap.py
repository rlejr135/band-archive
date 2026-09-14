"""Safe hand-off helpers for the pre-Alembic SQLite schema.

The first checked-in revision describes the schema that existed before
Alembic was introduced.  An existing database must never be blindly stamped:
stamping is safe only after its reflected schema exactly matches the current
model metadata.  This module intentionally does not run during application
startup.
"""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask import current_app
from sqlalchemy import inspect, text

from extensions import db


MIGRATIONS_DIRECTORY = Path(__file__).with_name('migrations')
BASELINE_REVISION = '20260914_01'
ALEMBIC_VERSION_TABLE = 'alembic_version'


class MigrationBootstrapError(RuntimeError):
    """Raised when an existing database is unsafe to stamp."""


def schema_differences(connection):
    """Return Alembic differences between an existing DB and model metadata."""
    context = MigrationContext.configure(
        connection,
        opts={
            'compare_type': True,
            'compare_server_default': True,
        },
    )
    return compare_metadata(context, db.metadata)


def _application_tables(connection):
    return sorted(
        table for table in inspect(connection).get_table_names()
        if table != ALEMBIC_VERSION_TABLE
    )


def _current_version(connection):
    tables = inspect(connection).get_table_names()
    if ALEMBIC_VERSION_TABLE not in tables:
        return None
    rows = connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
    return tuple(rows)


def _assert_current_schema(connection):
    tables = _application_tables(connection)
    if not tables:
        raise MigrationBootstrapError(
            'Database is empty. Run `flask db upgrade` instead of stamping it.'
        )

    differences = schema_differences(connection)
    if differences:
        raise MigrationBootstrapError(
            'Database schema does not match the checked-in baseline; refusing '
            f'to stamp. Differences: {differences!r}'
        )


def stamp_existing_current_schema():
    """Stamp an already-current schema after checking it is an exact match.

    The operation is explicit and idempotent.  Empty, partial, old, or newer
    schemas fail closed; callers must restore/migrate them deliberately.
    """
    engine = db.engine
    with engine.connect() as connection:
        _assert_current_schema(connection)
        version = _current_version(connection)
        if version is not None and version != (BASELINE_REVISION,):
            raise MigrationBootstrapError(
                f'Database is already stamped at {version!r}, not {BASELINE_REVISION!r}; '
                'refusing to rewrite Alembic history.'
            )
        if version == (BASELINE_REVISION,):
            return False

    migrate = current_app.extensions['migrate'].migrate
    config = migrate.get_config(str(MIGRATIONS_DIRECTORY))
    command.stamp(config, BASELINE_REVISION)
    return True


def create_migration_app(database_uri):
    """Create a migration-only Flask app with no legacy startup side effects.

    `create_app()` deliberately still calls `db.create_all()` during the
    compatibility period, so it must not be used to create a fresh Alembic
    database.  This isolated factory is used by the operator runner and tests.
    """
    from flask import Flask
    from flask_migrate import Migrate

    import models  # noqa: F401 -- register all model metadata before Alembic runs.

    app = Flask('anything-migration-runner')
    app.config.update(
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    Migrate(app, db, directory=str(MIGRATIONS_DIRECTORY))
    return app
