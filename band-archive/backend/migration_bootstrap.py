"""Safe hand-off helpers for the pre-Alembic SQLite schema.

The first checked-in revision describes the schema that existed before
Alembic was introduced.  An existing database must never be blindly stamped:
stamping is safe only after its reflected schema exactly matches the current
model metadata.  This module intentionally does not run during application
startup.
"""

from collections import Counter
from pathlib import Path
import re

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
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


def _normalize_type(column_type, dialect):
    return re.sub(r'\s+', '', column_type.compile(dialect=dialect)).upper()


def _normalize_sql(value):
    """Compare SQLite-reflected defaults/CHECK SQL without cosmetic syntax."""
    if value is None:
        return None
    normalized = str(value).strip()
    while normalized.startswith('(') and normalized.endswith(')'):
        normalized = normalized[1:-1].strip()
    normalized = normalized.replace('"', '').replace('`', '').replace('[', '').replace(']', '')
    return re.sub(r'\s+', '', normalized).lower()


def _metadata_default(column):
    if column.server_default is None:
        return None
    return _normalize_sql(column.server_default.arg)


def _normalize_default(value):
    normalized = _normalize_sql(value)
    if normalized is None:
        return None
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in "'\"":
        return normalized[1:-1]
    return normalized


def _expected_foreign_keys(table):
    expected = []
    for constraint in table.foreign_key_constraints:
        expected.append((
            constraint.name,
            tuple(column.name for column in constraint.columns),
            constraint.referred_table.name,
            tuple(element.column.name for element in constraint.elements),
            (constraint.ondelete or '').upper(),
            (constraint.onupdate or '').upper(),
        ))
    return Counter(expected)


def _reflected_foreign_keys(inspector, table_name):
    reflected = []
    for foreign_key in inspector.get_foreign_keys(table_name):
        options = foreign_key.get('options') or {}
        reflected.append((
            foreign_key.get('name'),
            tuple(foreign_key.get('constrained_columns') or ()),
            foreign_key.get('referred_table'),
            tuple(foreign_key.get('referred_columns') or ()),
            str(options.get('ondelete') or '').upper(),
            str(options.get('onupdate') or '').upper(),
        ))
    return Counter(reflected)


def _expected_unique_constraints(table):
    return Counter(
        (constraint.name, tuple(column.name for column in constraint.columns))
        for constraint in table.constraints
        if constraint.__class__.__name__ == 'UniqueConstraint'
    )


def _reflected_unique_constraints(inspector, table_name):
    constraints = inspector.get_unique_constraints(table_name)
    if constraints is None:
        raise MigrationBootstrapError(
            f'Dialect cannot reflect unique constraints for {table_name}; refusing to stamp.'
        )
    return Counter(
        (constraint.get('name'), tuple(constraint.get('column_names') or ()))
        for constraint in constraints
    )


def _expected_check_constraints(table):
    return Counter(
        (constraint.name, _normalize_sql(constraint.sqltext))
        for constraint in table.constraints
        if constraint.__class__.__name__ == 'CheckConstraint'
    )


def _reflected_check_constraints(inspector, table_name):
    constraints = inspector.get_check_constraints(table_name)
    if constraints is None:
        raise MigrationBootstrapError(
            f'Dialect cannot reflect CHECK constraints for {table_name}; refusing to stamp.'
        )
    reflected = []
    for constraint in constraints:
        if constraint.get('sqltext') is None:
            raise MigrationBootstrapError(
                f'Dialect returned an unreadable CHECK constraint for {table_name}; refusing to stamp.'
            )
        reflected.append((constraint.get('name'), _normalize_sql(constraint['sqltext'])))
    return Counter(reflected)


def _expected_indexes(table):
    return Counter(
        (index.name, tuple(column.name for column in index.columns), bool(index.unique))
        for index in table.indexes
    )


def _reflected_indexes(inspector, table_name):
    indexes = inspector.get_indexes(table_name)
    if indexes is None:
        raise MigrationBootstrapError(
            f'Dialect cannot reflect indexes for {table_name}; refusing to stamp.'
        )
    return Counter(
        (index.get('name'), tuple(index.get('column_names') or ()), bool(index.get('unique')))
        for index in indexes
    )


def schema_contract_differences(connection, inspector_override=None):
    """Fail-closed structural comparison beyond Alembic's best-effort diff.

    Alembic autogenerate intentionally omits or treats several SQLite
    constraints as implementation details.  Stamping a database is an audit
    boundary, so compare all schema elements required by current metadata.
    """
    inspector = inspector_override or inspect(connection)
    actual_tables = set(_application_tables(connection))
    expected_tables = set(db.metadata.tables)
    differences = []

    if actual_tables != expected_tables:
        differences.append(
            f'tables expected={sorted(expected_tables)!r} actual={sorted(actual_tables)!r}'
        )

    for table_name in sorted(expected_tables & actual_tables):
        table = db.metadata.tables[table_name]
        columns = inspector.get_columns(table_name)
        if columns is None:
            raise MigrationBootstrapError(
                f'Dialect cannot reflect columns for {table_name}; refusing to stamp.'
            )
        actual_columns = {column['name']: column for column in columns}
        expected_columns = {column.name: column for column in table.columns}
        if set(actual_columns) != set(expected_columns):
            differences.append(
                f'{table_name}.columns expected={sorted(expected_columns)!r} '
                f'actual={sorted(actual_columns)!r}'
            )
        for name in sorted(set(actual_columns) & set(expected_columns)):
            actual = actual_columns[name]
            expected = expected_columns[name]
            actual_type = _normalize_type(actual['type'], connection.dialect)
            expected_type = _normalize_type(expected.type, connection.dialect)
            actual_default = _normalize_default(actual.get('default'))
            expected_default = _normalize_default(_metadata_default(expected))
            actual_nullable = bool(actual.get('nullable'))
            if (actual_type, actual_nullable, actual_default) != (
                expected_type, expected.nullable, expected_default,
            ):
                differences.append(
                    f'{table_name}.{name} column expected='
                    f'({expected_type}, {expected.nullable}, {expected_default!r}) actual='
                    f'({actual_type}, {actual_nullable}, {actual_default!r})'
                )

        primary_key = inspector.get_pk_constraint(table_name)
        if primary_key is None or tuple(primary_key.get('constrained_columns') or ()) != tuple(
            column.name for column in table.primary_key.columns
        ):
            differences.append(f'{table_name}.primary_key does not match metadata')

        checks = (
            ('foreign_keys', _expected_foreign_keys(table), _reflected_foreign_keys(inspector, table_name)),
            ('unique_constraints', _expected_unique_constraints(table),
             _reflected_unique_constraints(inspector, table_name)),
            ('check_constraints', _expected_check_constraints(table),
             _reflected_check_constraints(inspector, table_name)),
            ('indexes', _expected_indexes(table), _reflected_indexes(inspector, table_name)),
        )
        for label, expected, actual in checks:
            if expected != actual:
                differences.append(
                    f'{table_name}.{label} expected={expected!r} actual={actual!r}'
                )
    return differences


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
            'Database is empty. Run `python migration_runner.py --database-url ... upgrade` instead.'
        )

    differences = schema_contract_differences(connection) + [
        f'alembic={difference!r}' for difference in schema_differences(connection)
    ]
    if differences:
        raise MigrationBootstrapError(
            'Database schema does not match the checked-in baseline; refusing '
            f'to stamp. Differences: {differences!r}'
        )


def _known_revisions():
    migrate = current_app.extensions['migrate'].migrate
    config = migrate.get_config(str(MIGRATIONS_DIRECTORY))
    script = ScriptDirectory.from_config(config)
    return {revision for revision in script.revision_map._revision_map if revision is not None}


def assert_upgrade_preflight():
    """Permit upgrade only for an empty DB or a valid stamped migration DB."""
    with db.engine.connect() as connection:
        application_tables = _application_tables(connection)
        version = _current_version(connection)
        if not application_tables:
            if version is None:
                return 'empty'
            raise MigrationBootstrapError(
                'Database has an alembic_version table but no application schema; '
                'refusing ambiguous upgrade.'
            )
        if version is None:
            raise MigrationBootstrapError(
                'Database has application tables but no Alembic revision; run '
                '`python migration_runner.py --database-url ... stamp-existing` first.'
            )
        if len(version) != 1 or not version[0] or version[0] not in _known_revisions():
            raise MigrationBootstrapError(
                f'Database has an unknown or invalid Alembic revision {version!r}; refusing upgrade.'
            )
        # While this repository has one baseline revision, a correctly stamped
        # database must also still match it structurally before it can advance.
        if version[0] == BASELINE_REVISION:
            _assert_current_schema(connection)
        return 'stamped'


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
