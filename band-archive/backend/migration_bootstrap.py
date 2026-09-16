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
from sqlalchemy import MetaData, inspect, text

from extensions import db


MIGRATIONS_DIRECTORY = Path(__file__).with_name('migrations')
BASELINE_REVISION = '20260914_01'
AUTH_REVISION = '20260914_02'
PHASE3_REVISION = '20260915_03'
PHASE3_HARDENED_REVISION = '20260916_04'
ALEMBIC_VERSION_TABLE = 'alembic_version'
BASELINE_TABLE_NAMES = (
    'announcement', 'comment', 'gallery_image', 'media', 'media_vote', 'member',
    'multipart_upload_part', 'multipart_upload_session', 'personal_log',
    'personal_log_multipart_upload_part', 'personal_log_multipart_upload_session',
    'rehearsal', 'rehearsal_songs', 'song', 'song_suggestion', 'song_vote',
)

# Columns added by the Phase 4 expand-only revision.  The v1 compatibility
# path intentionally builds the immutable baseline snapshot, not current ORM
# metadata, so a normal application start can never create a half-migrated
# tenant schema before Alembic has recorded its revision.
PHASE4_COLUMNS = {
    'band': {'is_legacy_import'},
    'announcement': {'band_id', 'created_by_membership_id', 'visibility'},
    'comment': {'band_id', 'created_by_membership_id', 'is_legacy'},
    'gallery_image': {'band_id', 'created_by_membership_id', 'visibility', 'object_key'},
    'media': {'band_id', 'created_by_membership_id', 'visibility', 'object_key', 'audio_object_key',
              'video_720_object_key'},
    'media_vote': {'band_id', 'created_by_membership_id', 'is_legacy'},
    'member': {'band_id', 'linked_user_id'},
    'multipart_upload_session': {'band_id', 'created_by_membership_id', 'is_legacy'},
    'personal_log': {'band_id', 'created_by_membership_id', 'visibility', 'object_key', 'audio_object_key'},
    'personal_log_multipart_upload_session': {'band_id', 'created_by_membership_id', 'is_legacy'},
    'rehearsal': {'band_id', 'created_by_membership_id', 'visibility'},
    'song': {'band_id', 'created_by_membership_id', 'visibility', 'sheet_music_object_key'},
    'song_suggestion': {'band_id', 'created_by_membership_id', 'visibility'},
    'song_vote': {'band_id', 'created_by_membership_id', 'is_legacy'},
}


class MigrationBootstrapError(RuntimeError):
    """Raised when an existing database is unsafe to stamp."""


def schema_differences(connection, metadata=None):
    """Return Alembic differences between an existing DB and model metadata."""
    context = MigrationContext.configure(
        connection,
        opts={
            'compare_type': True,
            'compare_server_default': True,
        },
    )
    return compare_metadata(context, metadata or db.metadata)


def baseline_metadata():
    """The pre-Alembic schema snapshot used only for an explicit legacy stamp."""
    metadata = MetaData()
    for table_name in BASELINE_TABLE_NAMES:
        db.metadata.tables[table_name].to_metadata(metadata)
    for table_name, columns in PHASE4_COLUMNS.items():
        if table_name not in metadata.tables:
            continue
        table = metadata.tables[table_name]
        for index in list(table.indexes):
            if any(column.name in columns for column in index.columns):
                table.indexes.remove(index)
        for constraint in list(table.constraints):
            constrained = {column.name for column in getattr(constraint, 'columns', ())}
            if constrained & columns or (getattr(constraint, 'name', '') or '').startswith('ck_') and 'visibility' in str(
                    getattr(constraint, 'sqltext', '')):
                table.constraints.remove(constraint)
        for column_name in columns:
            column = table.c.get(column_name)
            if column is not None:
                # A copied column retains its ForeignKey object even after its
                # table-level constraint was removed above.  Detach it before
                # removing the column so the v1 snapshot has no dangling
                # reference to Phase 3/4 tables.
                for foreign_key in list(column.foreign_keys):
                    table.constraints.discard(foreign_key.constraint)
                    table.foreign_keys.discard(foreign_key)
                    column.foreign_keys.discard(foreign_key)
                table._columns.remove(column)
    return metadata


def auth_metadata():
    """Exact revision-02 schema snapshot for safe upgrades to Phase 3."""
    metadata = baseline_metadata()
    for table_name in ('app_user', 'auth_bootstrap_state'):
        db.metadata.tables[table_name].to_metadata(metadata)
    return metadata


def phase3_metadata():
    """Exact immutable revision-03 snapshot, before hardening revision 04."""
    metadata = auth_metadata()
    for table_name in ('band', 'band_membership', 'band_creation_request', 'band_invite_code', 'audit_event'):
        db.metadata.tables[table_name].to_metadata(metadata)
    _restore_pre_phase4_band(metadata)
    # These partial indexes belong to revision 04.  Do not retroactively
    # require them when validating a legitimately recorded revision 03 DB.
    for table_name, index_name in (
        ('band_membership', 'uq_band_membership_active_owner'),
        ('band_creation_request', 'uq_band_creation_request_pending_user_slug'),
    ):
        table = metadata.tables[table_name]
        index = next((item for item in table.indexes if item.name == index_name), None)
        if index is not None:
            table.indexes.remove(index)
    return metadata


def phase3_hardened_metadata():
    """Exact immutable revision-04 snapshot, before the Phase 4 expand."""
    metadata = auth_metadata()
    for table_name in ('band', 'band_membership', 'band_creation_request', 'band_invite_code', 'audit_event'):
        db.metadata.tables[table_name].to_metadata(metadata)
    _restore_pre_phase4_band(metadata)
    return metadata


def _restore_pre_phase4_band(metadata):
    """Remove the ownerless-import exception from immutable v03/v04 views."""
    band = metadata.tables['band']
    band.c.created_by_user_id.nullable = False
    band._columns.remove(band.c.is_legacy_import)


def _normalize_type(column_type, dialect):
    return re.sub(r'\s+', '', column_type.compile(dialect=dialect)).upper()


def _postgres_float_family(column_type, dialect):
    """Return PostgreSQL's effective float width, or None when not a float.

    PostgreSQL reflects an unqualified SQLAlchemy ``Float`` as
    ``DOUBLE PRECISION`` even though the model renders it as ``FLOAT``.  Those
    are the same PostgreSQL type.  ``REAL`` and precision-qualified floats are
    deliberately left distinct so this exception cannot hide a real drift.
    """
    rendered = _normalize_type(column_type, dialect)
    if rendered in {'FLOAT', 'DOUBLEPRECISION'} and getattr(column_type, 'precision', None) is None:
        return 'float64'
    if rendered == 'REAL':
        return 'float32'
    return None


def types_are_equivalent(expected, actual, dialect):
    """Compare reflected types while retaining dialect-specific safety rules."""
    expected_rendered = _normalize_type(expected, dialect)
    actual_rendered = _normalize_type(actual, dialect)
    if expected_rendered == actual_rendered:
        return True
    if dialect.name == 'postgresql':
        expected_family = _postgres_float_family(expected, dialect)
        actual_family = _postgres_float_family(actual, dialect)
        return expected_family is not None and expected_family == actual_family
    return False


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
        name = foreign_key.get('name')
        # Phase 4 batch rebuild needs explicit names on SQLite, while the ORM
        # models intentionally keep portable anonymous ForeignKey objects.
        # The FK shape—not a dialect-generated label—is the contract here.
        if isinstance(name, str) and name.startswith('fk_'):
            name = None
        reflected.append((
            name,
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


def schema_contract_differences(connection, inspector_override=None, metadata=None):
    """Fail-closed structural comparison beyond Alembic's best-effort diff.

    Alembic autogenerate intentionally omits or treats several SQLite
    constraints as implementation details.  Stamping a database is an audit
    boundary, so compare all schema elements required by current metadata.
    """
    inspector = inspector_override or inspect(connection)
    actual_tables = set(_application_tables(connection))
    metadata = metadata or db.metadata
    expected_tables = set(metadata.tables)
    differences = []

    if actual_tables != expected_tables:
        differences.append(
            f'tables expected={sorted(expected_tables)!r} actual={sorted(actual_tables)!r}'
        )

    for table_name in sorted(expected_tables & actual_tables):
        table = metadata.tables[table_name]
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
            if (not types_are_equivalent(expected.type, actual['type'], connection.dialect)
                    or (actual_nullable, actual_default) != (expected.nullable, expected_default)):
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
    differences.extend(_partial_index_differences(connection, metadata))
    # SQLAlchemy's generic inspector does not reliably expose trigger bodies.
    # Once a DB has recorded the Phase 3 head, require the database-level
    # append-only guard explicitly rather than trusting the ORM event hook.
    if ('audit_event' in actual_tables and _current_version(connection) == (migration_head_revision(),)):
        differences.extend(_audit_trigger_differences(connection))
    return differences


def _partial_index_differences(connection, metadata):
    """Verify predicates that generic SQLAlchemy index reflection omits."""
    expected = []
    for table in metadata.tables.values():
        for index in table.indexes:
            where = index.dialect_options['sqlite'].get('where')
            if where is None:
                where = index.dialect_options['postgresql'].get('where')
            if where is not None:
                # SQLite compares its reflected SQL text strictly. PostgreSQL
                # gets a semantic parse below; pre-normalizing would erase the
                # word boundary that distinguishes ``AND`` from identifiers.
                predicate = str(where) if connection.dialect.name == 'postgresql' else _normalize_sql(where)
                expected.append((table.name, index.name, predicate))
    if not expected:
        return []
    if connection.dialect.name == 'sqlite':
        rows = connection.execute(text("SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index'"))
        actual = {(row[1], row[0]): _normalize_sql(row[2]) for row in rows}
        return [f'{table}.{name} partial predicate is missing or invalid'
                for table, name, predicate in expected
                if f'where{predicate}' not in actual.get((table, name), '')]
    if connection.dialect.name == 'postgresql':
        rows = connection.execute(text("""
            SELECT indexes.tablename, indexes.indexname,
                   pg_get_expr(ind.indpred, ind.indrelid) AS predicate
            FROM pg_indexes AS indexes
            JOIN pg_class AS cls ON cls.relname = indexes.indexname
            JOIN pg_namespace AS ns
              ON ns.oid = cls.relnamespace AND ns.nspname = indexes.schemaname
            JOIN pg_index AS ind ON ind.indexrelid = cls.oid
            WHERE indexes.schemaname = ANY(current_schemas(false))
        """))
        actual = {(row[0], row[1]): _canonical_postgres_partial_predicate(row[2]) for row in rows}
        return [f'{table}.{name} partial predicate is missing or invalid'
                for table, name, predicate in expected
                if _canonical_postgres_partial_predicate(predicate) != actual.get((table, name))]
    return ['partial index predicates cannot be verified for this database dialect']


def _canonical_postgres_partial_predicate(value):
    """Parse the deliberately tiny predicate language used by revision 04.

    PostgreSQL's ``pg_get_expr`` adds parentheses and ``::text`` casts, whose
    formatting changes by server version.  Do not use a loose substring test:
    parse only equality atoms over ``role``/``status`` joined by AND.  Any
    other expression (notably OR, a missing atom, or an unfamiliar cast) is
    rejected by returning ``None``.
    """
    if not isinstance(value, str):
        return None
    expression = value.strip().casefold()
    atoms = _split_postgres_predicate_and(expression)
    if not atoms:
        return None
    parsed = set()
    for atom in atoms:
        parsed_atom = _parse_postgres_predicate_atom(atom)
        if parsed_atom is None:
            return None
        parsed.add(parsed_atom)
    # Duplicate conditions are not semantically the intended exact predicate.
    if len(parsed) != len(atoms):
        return None
    parsed = frozenset(parsed)
    # The metadata has exactly these two controlled predicates.  Keep the
    # PostgreSQL-side parser deliberately narrower than SQL: accepting an
    # arbitrary subset would hide a weakened or swapped index predicate.
    if parsed not in {
        frozenset({('role', 'owner'), ('status', 'active')}),
        frozenset({('status', 'pending')}),
    }:
        return None
    return parsed


def _strip_sql_parentheses(value):
    """Remove only parentheses that enclose the entire SQL fragment."""
    value = value.strip()
    while value.startswith('(') and value.endswith(')'):
        depth = 0
        quote = False
        encloses_all = True
        for index, character in enumerate(value):
            if character == "'":
                # The tiny predicate language has no escaped literal quotes.
                quote = not quote
            elif not quote and character == '(':
                depth += 1
            elif not quote and character == ')':
                depth -= 1
                if depth == 0 and index != len(value) - 1:
                    encloses_all = False
                    break
            if depth < 0:
                return ''
        if quote or depth != 0 or not encloses_all:
            break
        value = value[1:-1].strip()
    return value


def _split_postgres_predicate_and(expression):
    """Split the controlled predicate grammar on top-level ``AND`` only."""
    expression = _strip_sql_parentheses(expression)
    if not expression:
        return None
    atoms = []
    start = 0
    depth = 0
    quote = False
    index = 0
    while index < len(expression):
        character = expression[index]
        if character == "'":
            quote = not quote
            index += 1
            continue
        if not quote:
            if character == '(':
                depth += 1
            elif character == ')':
                depth -= 1
                if depth < 0:
                    return None
            elif depth == 0 and expression[index:index + 3] == 'and':
                before = expression[index - 1] if index else ' '
                after = expression[index + 3] if index + 3 < len(expression) else ' '
                if not (before.isalnum() or before == '_') and not (after.isalnum() or after == '_'):
                    atoms.append(expression[start:index].strip())
                    start = index + 3
                    index += 3
                    continue
        index += 1
    if quote or depth != 0:
        return None
    atoms.append(expression[start:].strip())
    return atoms if all(atoms) else None


def _postgres_predicate_operand(value, literal=False):
    """Accept a known value, optionally wrapped in PostgreSQL's ``::text``."""
    value = _strip_sql_parentheses(value)
    cast = re.fullmatch(r'(.+?)\s*::\s*text', value)
    if cast:
        value = _strip_sql_parentheses(cast.group(1))
    # A different (or nested) cast is intentionally not normalized away.
    if '::' in value:
        return None
    if literal:
        match = re.fullmatch(r"'(owner|active|pending)'", value)
        return match.group(1) if match else None
    match = re.fullmatch(r'(?:[a-z_][a-z0-9_]*\.)?(role|status)', value)
    return match.group(1) if match else None


def _parse_postgres_predicate_atom(atom):
    atom = _strip_sql_parentheses(atom)
    # Equality is the only comparison accepted.  A top-level split keeps a
    # quoted literal or nested parentheses from masquerading as an operator.
    depth = 0
    quote = False
    equals = []
    for index, character in enumerate(atom):
        if character == "'":
            quote = not quote
        elif not quote and character == '(':
            depth += 1
        elif not quote and character == ')':
            depth -= 1
        elif not quote and depth == 0 and character == '=':
            equals.append(index)
    if quote or depth != 0 or len(equals) != 1:
        return None
    left = _postgres_predicate_operand(atom[:equals[0]])
    right = _postgres_predicate_operand(atom[equals[0] + 1:], literal=True)
    if left is None or right is None:
        return None
    return left, right


def _audit_trigger_differences(connection):
    """Return explicit append-only trigger contract defects, fail closed."""
    dialect = connection.dialect.name
    if dialect == 'sqlite':
        rows = connection.execute(text(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name IN ('audit_event_no_update', 'audit_event_no_delete')"
        )).all()
        actual = {name: (sql or '').upper() for name, sql in rows}
        expected = {
            'audit_event_no_update': 'BEFORE UPDATE ON AUDIT_EVENT',
            'audit_event_no_delete': 'BEFORE DELETE ON AUDIT_EVENT',
        }
        return [f'audit_event trigger {name} is missing or invalid' for name, clause in expected.items()
                if clause not in actual.get(name, '') or 'RAISE(ABORT' not in actual.get(name, '')]
    if dialect == 'postgresql':
        rows = connection.execute(text("""
            SELECT trigger_name, action_statement
            FROM information_schema.triggers
            WHERE event_object_table = 'audit_event'
              AND trigger_name IN ('audit_event_no_update', 'audit_event_no_delete')
        """)).all()
        actual = {name: (statement or '').lower() for name, statement in rows}
        return [f'audit_event trigger {name} is missing or invalid'
                for name in ('audit_event_no_update', 'audit_event_no_delete')
                if 'audit_event_append_only' not in actual.get(name, '')]
    # An unknown dialect cannot prove the invariant.
    return ['audit_event triggers cannot be verified for this database dialect']


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


def _assert_current_schema(connection, metadata=None):
    tables = _application_tables(connection)
    if not tables:
        raise MigrationBootstrapError(
            'Database is empty. Run `python migration_runner.py --database-url ... upgrade` instead.'
        )

    metadata = metadata or db.metadata
    differences = schema_contract_differences(connection, metadata=metadata) + [
        f'alembic={difference!r}' for difference in schema_differences(connection, metadata=metadata)
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


def migration_head_revision():
    """Return the checked-in Alembic head for runtime readiness checks."""
    migrate = current_app.extensions['migrate'].migrate
    config = migrate.get_config(str(MIGRATIONS_DIRECTORY))
    return ScriptDirectory.from_config(config).get_current_head()


def metadata_for_recorded_revision(revision):
    """Return the exact metadata expected for a known recorded revision."""
    if revision == BASELINE_REVISION:
        return baseline_metadata()
    if revision == AUTH_REVISION:
        return auth_metadata()
    if revision == PHASE3_REVISION:
        return phase3_metadata()
    if revision == PHASE3_HARDENED_REVISION:
        return phase3_hardened_metadata()
    if revision == migration_head_revision():
        return db.metadata
    raise MigrationBootstrapError(
        f'No exact schema snapshot is available for Alembic revision {revision!r}; refusing upgrade.'
    )


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
        _assert_current_schema(connection, metadata=metadata_for_recorded_revision(version[0]))
        return 'stamped'


def stamp_existing_current_schema():
    """Stamp an already-current schema after checking it is an exact match.

    The operation is explicit and idempotent.  Empty, partial, old, or newer
    schemas fail closed; callers must restore/migrate them deliberately.
    """
    engine = db.engine
    with engine.connect() as connection:
        # A legacy DB must match the old baseline, not the current head.  This
        # prevents a new model table created by legacy `create_all()` from
        # being incorrectly stamped as if its Alembic migration had run.
        _assert_current_schema(connection, metadata=baseline_metadata())
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
