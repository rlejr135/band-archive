from io import StringIO
import importlib
from collections import Counter

from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
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
    metadata_for_recorded_revision,
    PHASE13_REVISION,
    phase4_metadata,
    _canonical_postgres_partial_predicate,
    _canonical_postgres_check_constraint,
    _constraints_are_equivalent,
    _defaults_are_equivalent,
    _metadata_default,
    _reflected_indexes,
    _reflected_check_constraints,
    _audit_trigger_differences,
    _expected_check_constraints,
    _postgres_serial_sequence_is_owned,
    _normalize_postgres_check_for_table,
    _partial_index_differences,
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
    arguments = command.split() if isinstance(command, str) else command
    return CliRunner().invoke(cli, ['--database-url', _database_url(database_path), *arguments])


def _version(app):
    with app.app_context():
        with db.engine.connect() as connection:
            return connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()


def _dispose(app):
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _upgrade_through_read_scope_contract(database_path):
    """Advance legacy fixtures through the explicit Phase 4 operator gate."""
    staged = _runner_command(database_path, 'upgrade')
    assert staged.exit_code != 0, staged.output
    assert 'read-scope contract' in staged.output
    backfilled = _runner_command(database_path, 'tenant-backfill --apply')
    assert backfilled.exit_code == 0, backfilled.output
    return _runner_command(database_path, 'upgrade')


def _offline_postgres_revision_sql(operation):
    """Compile a revision directly, without a PostgreSQL driver or server."""
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
        'SUPABASE_PUBLISHABLE_KEY': 'sb_publishable_test_only',
        'BOOTSTRAP_PLATFORM_ADMIN_EMAIL': 'admin@example.com',
    })


def test_fresh_database_upgrade_creates_current_baseline(tmp_path):
    database_path = tmp_path / 'fresh.db'
    # The contract revision stops at v05 until the operator records a
    # validated tenant backfill, even for an otherwise empty fresh database.
    blocked = _runner_command(database_path, 'upgrade')
    assert blocked.exit_code != 0
    assert 'read-scope contract' in blocked.output
    assert _runner_command(database_path, 'tenant-backfill --apply').exit_code == 0
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


def test_email_outbox_revision_metadata_upgrade_and_populated_downgrade_guard(tmp_path):
    database_path = tmp_path / 'email-outbox-foundation.db'
    staged = _runner_command(database_path, 'upgrade')
    assert staged.exit_code != 0 and 'read-scope contract' in staged.output
    assert _runner_command(database_path, 'tenant-backfill --apply').exit_code == 0
    upgraded = _runner_command(database_path, 'upgrade')
    assert upgraded.exit_code == 0, upgraded.output

    app = _migration_app(database_path)
    try:
        with app.app_context():
            assert migration_head_revision() == '20261024_24'
            assert _version(app) == migration_head_revision()
            with db.engine.connect() as connection:
                assert 'band_request_email_outbox' in inspect(connection).get_table_names()
                assert 'r2_deletion_task' in inspect(connection).get_table_names()
                assert 'policy_acceptance_receipt' in inspect(connection).get_table_names()
                # The receipt PK is an AppUser FK, not a generated ID; on
                # PostgreSQL this must not acquire an implicit serial sequence.
                assert db.metadata.tables['policy_acceptance_receipt'].c.user_id.autoincrement is False
                assert schema_differences(connection) == []
                assert schema_contract_differences(connection) == []

            # Revision 13 did not change table structure. Its historical
            # snapshot must remain usable after the new ORM table is defined.
            downgrade(revision=PHASE13_REVISION, directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.connect() as connection:
                assert schema_differences(
                    connection, metadata=metadata_for_recorded_revision(PHASE13_REVISION),
                ) == []
                assert 'band_request_email_outbox' not in inspect(connection).get_table_names()

            upgrade(directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO band_request_email_outbox "
                    "(decision, next_attempt_at, created_at, updated_at) "
                    "VALUES ('approved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ))
            migrate = app.extensions['migrate'].migrate
            config = migrate.get_config(str(MIGRATIONS_DIRECTORY))
            with pytest.raises(RuntimeError, match='Refusing to drop a populated email outbox'):
                from alembic import command
                command.downgrade(config, PHASE13_REVISION)
            # The empty queue downgrade completed before the populated
            # email-outbox guard stopped the following revision rollback.
            assert _version(app) == '20261014_14'
            with db.engine.connect() as connection:
                assert connection.execute(text(
                    'SELECT count(*) FROM band_request_email_outbox'
                )).scalar_one() == 1
    finally:
        _dispose(app)


def test_member_profile_revision_18_preserves_v17_member_and_log_rows(tmp_path):
    database_path = tmp_path / 'member-profile-v17-data.db'
    upgraded = _upgrade_through_read_scope_contract(database_path)
    assert upgraded.exit_code == 0, upgraded.output
    app = _migration_app(database_path)
    try:
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO band (name, slug, created_at, updated_at) "
                    "VALUES ('Profile preservation', 'profile-preservation', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ))
                band_id = connection.execute(text(
                    "SELECT id FROM band WHERE slug='profile-preservation'"
                )).scalar_one()
            downgrade(revision='20261017_17', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO member (band_id, linked_user_id, name, instrument) "
                    "VALUES (:band_id, NULL, 'Legacy one', 'legacy bass'), "
                    "(:band_id, NULL, 'Legacy two', 'custom instrument')"
                ), {'band_id': band_id})
                member_id = connection.execute(text(
                    "SELECT id FROM member WHERE band_id=:band_id AND name='Legacy one'"
                ), {'band_id': band_id}).scalar_one()
                connection.execute(text(
                    "INSERT INTO personal_log (band_id, member_id, title, filename, file_type, "
                    "transcoding_status, processing_attempts, created_at) "
                    "VALUES (:band_id, :member_id, 'Old log', 'old.wav', 'audio', "
                    "'not_required', 0, CURRENT_TIMESTAMP)"
                ), {'band_id': band_id, 'member_id': member_id})
            upgrade(directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.connect() as connection:
                rows = connection.execute(text(
                    "SELECT id, name, instrument, linked_user_id, biography, positions_json, "
                    "custom_position, profile_revision FROM member WHERE band_id=:band_id ORDER BY name"
                ), {'band_id': band_id}).all()
                assert [(row.name, row.instrument, row.linked_user_id, row.biography,
                         row.positions_json, row.custom_position, row.profile_revision) for row in rows] == [
                    ('Legacy one', 'legacy bass', None, '', '[]', '', 0),
                    ('Legacy two', 'custom instrument', None, '', '[]', '', 0),
                ]
                assert connection.execute(text(
                    "SELECT member_id, title FROM personal_log WHERE band_id=:band_id"
                ), {'band_id': band_id}).all() == [(member_id, 'Old log')]
                assert schema_contract_differences(connection) == []
    finally:
        _dispose(app)


def test_revision_five_postgres_offline_upgrade_and_downgrade_compile_without_reflection(monkeypatch):
    """Phase 4's PostgreSQL path must work for release SQL generation offline."""
    revision = importlib.import_module('migrations.versions.20260916_05_expand_tenant_schema')
    monkeypatch.setattr(revision.context, 'is_offline_mode', lambda: True)

    upgrade_sql = _offline_postgres_revision_sql(revision.upgrade)
    downgrade_sql = _offline_postgres_revision_sql(revision.downgrade)

    assert 'ALTER TABLE song ADD COLUMN band_id INTEGER' in upgrade_sql
    assert 'ALTER TABLE media ADD CONSTRAINT fk_media_band FOREIGN KEY(band_id) REFERENCES band (id)' in upgrade_sql
    assert 'ALTER TABLE song ADD CONSTRAINT ck_song_visibility CHECK' in upgrade_sql
    assert 'CREATE TABLE tenant_backfill_state' in upgrade_sql
    assert 'ALTER TABLE song DROP CONSTRAINT ck_song_visibility' in downgrade_sql
    assert 'ALTER TABLE media DROP CONSTRAINT fk_media_band' in downgrade_sql
    assert 'DROP TABLE tenant_backfill_state' in downgrade_sql
    assert '_alembic_tmp' not in upgrade_sql
    assert '_alembic_tmp' not in downgrade_sql


def test_revision_five_sqlite_upgrade_downgrade_and_repeat_keep_exact_metadata(tmp_path):
    """SQLite keeps its batch path while the pre-backfill inverse stays usable."""
    database_path = tmp_path / 'revision-five-roundtrip.db'
    app = _migration_app(database_path)
    try:
        with app.app_context():
            upgrade(revision='20260916_04', directory=str(MIGRATIONS_DIRECTORY))
            upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
            assert _version(app) == '20260916_05'
            with db.engine.connect() as connection:
                assert schema_differences(connection, metadata=phase4_metadata()) == []
                assert schema_contract_differences(connection, metadata=phase4_metadata()) == []

            downgrade(revision='20260916_04', directory=str(MIGRATIONS_DIRECTORY))
            assert _version(app) == '20260916_04'
            upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
            upgrade(revision='20260916_05', directory=str(MIGRATIONS_DIRECTORY))
            with db.engine.connect() as connection:
                assert schema_differences(connection, metadata=phase4_metadata()) == []
                assert schema_contract_differences(connection, metadata=phase4_metadata()) == []
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

        upgraded = _upgrade_through_read_scope_contract(database_path)
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
        result = _upgrade_through_read_scope_contract(database_path)
        assert result.exit_code == 0, result.output
        with app.app_context():
            assert _version(app) == migration_head_revision()
    finally:
        _dispose(app)


def test_recorded_revision_three_requires_hardening_upgrade_before_v2_is_ready(tmp_path):
    """The immutable v03 schema is valid, but cannot serve v2 until v04."""
    database_path = tmp_path / 'revision-three.db'
    app = _migration_app(database_path)
    try:
        with app.app_context():
            upgrade(revision='20260915_03', directory=str(MIGRATIONS_DIRECTORY))
            assert _version(app) == '20260915_03'
    finally:
        _dispose(app)

    runtime_before = create_app(_runtime_config(database_path), start_worker=False)
    try:
        assert runtime_before.config['BAND_API_SCHEMA_READY'] is False
        assert runtime_before.test_client().get('/api/v2/public/bands').status_code == 503
        blocked = runtime_before.test_client().post('/songs', json={'title': 'blocked', 'artist': 'blocked'})
        assert blocked.status_code == 503
        assert blocked.get_json()['error'] == 'tenant_schema_unavailable'
        with runtime_before.app_context():
            assert db.session.execute(text('SELECT count(*) FROM song')).scalar_one() == 0
    finally:
        _dispose(runtime_before)

    first = _runner_command(database_path, 'upgrade')
    second = _runner_command(database_path, 'upgrade')
    assert first.exit_code != 0
    assert second.exit_code != 0
    assert 'read-scope contract' in first.output
    assert 'read-scope contract' in second.output
    assert _runner_command(database_path, 'tenant-backfill --dry-run').exit_code == 0
    applied = _runner_command(database_path, 'tenant-backfill --apply')
    assert applied.exit_code == 0, applied.output
    assert _runner_command(database_path, 'upgrade').exit_code == 0
    hardened = _migration_app(database_path)
    try:
        with hardened.app_context():
            assert _version(hardened) == migration_head_revision()
            indexes = {item['name'] for item in inspect(db.engine).get_indexes('band_membership')}
            request_indexes = {item['name'] for item in inspect(db.engine).get_indexes('band_creation_request')}
            assert 'uq_band_membership_active_owner' in indexes
            assert 'uq_band_creation_request_pending_user_slug' in request_indexes
    finally:
        _dispose(hardened)

    runtime_after = create_app(_runtime_config(database_path), start_worker=False)
    try:
        assert runtime_after.config['BAND_API_SCHEMA_READY'] is True
        assert runtime_after.test_client().get('/api/v2/public/bands').status_code == 200
        created = runtime_after.test_client().post('/songs', json={'title': 'legacy write', 'artist': 'band'})
        assert created.status_code == 201
        with runtime_after.app_context():
            assert db.session.execute(text('SELECT band_id FROM song')).scalar_one() == db.session.execute(
                text('SELECT id FROM band WHERE is_legacy_import = 1')
            ).scalar_one()
    finally:
        _dispose(runtime_after)


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


@pytest.mark.parametrize(
    ('index_name', 'table_name', 'columns', 'weak_predicate'),
    [
        ('uq_band_membership_active_owner', 'band_membership', 'band_id', "role = 'owner'"),
        ('uq_band_creation_request_pending_user_slug', 'band_creation_request',
         'requested_by_user_id, slug', "status IN ('pending', 'active')"),
    ],
)
def test_preflight_rejects_weakened_partial_index_predicate(
    tmp_path, index_name, table_name, columns, weak_predicate,
):
    database_path = tmp_path / f'weak-{index_name}.db'
    assert _upgrade_through_read_scope_contract(database_path).exit_code == 0
    app = _migration_app(database_path)
    try:
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text(f'DROP INDEX {index_name}'))
                connection.execute(text(
                    f'CREATE UNIQUE INDEX {index_name} ON {table_name} ({columns}) WHERE {weak_predicate}'
                ))
        result = _runner_command(database_path, 'upgrade')
        assert result.exit_code != 0
        assert f'{table_name}.{index_name} partial predicate' in result.output
    finally:
        _dispose(app)


def test_postgres_float_and_double_precision_are_semantically_equivalent():
    dialect = postgresql.dialect()
    assert types_are_equivalent(sa.Float(), postgresql.DOUBLE_PRECISION(), dialect)
    assert not types_are_equivalent(sa.Float(), postgresql.REAL(), dialect)
    assert not types_are_equivalent(sa.Float(), sa.Integer(), dialect)
    assert not types_are_equivalent(sa.Integer(), sa.String(20), dialect)


def test_postgres_default_normalization_is_limited_to_the_column_contract():
    metadata = sa.MetaData()
    table = sa.Table(
        'audit_event', metadata,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('quota', sa.BigInteger, nullable=False, server_default='21474836480'),
    )
    dialect = postgresql.dialect()
    resolver = lambda connection, table_name, column_name, sequence_name: (
        table_name, column_name, sequence_name,
    ) == ('audit_event', 'id', 'audit_event_id_seq')
    assert _defaults_are_equivalent(
        None, "nextval('audit_event_id_seq'::regclass)", table.c.id, 'audit_event', dialect,
        sequence_resolver=resolver,
    )
    for invalid in (
        "nextval('other_id_seq'::regclass)", "nextval('audit_event_other_seq'::regclass)",
        "nextval('public.audit_event_id_seq'::regclass)",
        "nextval('\"audit_event_id_seq\"'::regclass)",
        "nextval('audit_event_id_seq'::text)", None,
    ):
        assert not _defaults_are_equivalent(
            None, invalid, table.c.id, 'audit_event', dialect, sequence_resolver=resolver,
        )
    assert not _defaults_are_equivalent(
        None, "nextval('audit_event_id_seq'::regclass)", table.c.id, 'audit_event', dialect,
        sequence_resolver=lambda *args: False,
    )
    assert _defaults_are_equivalent('0', 'false', table.c.enabled, 'audit_event', dialect)
    assert _defaults_are_equivalent('1', 'true', table.c.enabled, 'audit_event', dialect)
    assert not _defaults_are_equivalent('0', 'true', table.c.enabled, 'audit_event', dialect)
    assert _defaults_are_equivalent('active', "'active'::character varying", table.c.status, 'audit_event', dialect)
    assert not _defaults_are_equivalent('active', "'other'::character varying", table.c.status, 'audit_event', dialect)
    assert not _defaults_are_equivalent('active', "'active'::integer", table.c.status, 'audit_event', dialect)
    assert not _defaults_are_equivalent('active', 'current_user', table.c.status, 'audit_event', dialect)
    assert _defaults_are_equivalent('21474836480', '21474836480::bigint', table.c.quota, 'audit_event', dialect)


def test_member_positions_default_requires_a_valid_empty_json_array_literal():
    metadata = sa.MetaData()
    member = sa.Table(
        'member', metadata,
        sa.Column('positions_json', sa.Text, nullable=False, server_default='[]'),
    )
    column = member.c.positions_json
    assert _metadata_default(column) == '[]'
    assert _defaults_are_equivalent('[]', "'[]'", column, 'member', sqlite.dialect())
    assert _defaults_are_equivalent('[]', "'[ ]'", column, 'member', sqlite.dialect())
    for invalid in ("''", "' '", 'NULL', None):
        assert not _defaults_are_equivalent('[]', invalid, column, 'member', sqlite.dialect())
    assert _defaults_are_equivalent('[]', "'[]'::text", column, 'member', postgresql.dialect())
    assert not _defaults_are_equivalent('[]', "''::text", column, 'member', postgresql.dialect())


def test_postgres_serial_default_does_not_apply_to_composite_pk_auto_columns():
    dialect = postgresql.dialect()
    composite = sa.Table(
        'rehearsal_songs', sa.MetaData(),
        sa.Column('rehearsal_id', sa.Integer, primary_key=True),
        sa.Column('song_id', sa.Integer, primary_key=True),
    )
    resolver = lambda *args: True
    assert _defaults_are_equivalent(None, None, composite.c.rehearsal_id, 'rehearsal_songs', dialect,
                                   sequence_resolver=resolver)
    assert not _defaults_are_equivalent(
        None, "nextval('rehearsal_songs_rehearsal_id_seq'::regclass)", composite.c.rehearsal_id,
        'rehearsal_songs', dialect, sequence_resolver=resolver,
    )
    explicit = sa.Table(
        'explicit_composite', sa.MetaData(),
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('part', sa.Integer, primary_key=True),
    )
    assert _defaults_are_equivalent(
        None, "nextval('explicit_composite_id_seq'::regclass)", explicit.c.id,
        'explicit_composite', dialect, sequence_resolver=resolver,
    )
    assert not _defaults_are_equivalent(None, None, explicit.c.id, 'explicit_composite', dialect,
                                        sequence_resolver=resolver)


def test_postgres_constraint_names_only_wildcard_anonymous_metadata():
    anonymous = Counter({(None, ('band_id',), 'band', ('id',), 'CASCADE', '', (None, None)): 1})
    generated = Counter({('band_membership_band_id_fkey', ('band_id',), 'band', ('id',), 'CASCADE', '', (None, None)): 1})
    assert _constraints_are_equivalent(anonymous, generated)
    named = Counter({('fk_membership_band', ('band_id',), 'band', ('id',), 'CASCADE', '', (None, None)): 1})
    assert not _constraints_are_equivalent(named, generated)
    wrong_action = Counter({('band_membership_band_id_fkey', ('band_id',), 'band', ('id',), 'SET NULL', '', (None, None)): 1})
    assert not _constraints_are_equivalent(anonymous, wrong_action)
    wrong_target = Counter({('band_membership_band_id_fkey', ('band_id',), 'app_user', ('id',), 'CASCADE', '', (None, None)): 1})
    assert not _constraints_are_equivalent(anonymous, wrong_target)


def test_postgres_check_normalization_handles_only_literal_in_and_any_forms():
    table = sa.Table('visibility_test', sa.MetaData(), sa.Column('visibility', sa.String(20)))
    expected = _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint("visibility IN ('PUBLIC', 'MEMBERS')"), table,
    )
    actual = _normalize_postgres_check_for_table(_canonical_postgres_check_constraint(
        "((visibility)::text = ANY ((ARRAY['MEMBERS'::character varying, 'PUBLIC'::character varying])::text[]))"
    ), table)
    assert expected == actual
    assert _canonical_postgres_check_constraint("visibility IN ('PUBLIC')") != expected
    assert _canonical_postgres_check_constraint("visibility IN ('PUBLIC', 'MEMBERS') OR visibility = 'PRIVATE'") is None
    assert _canonical_postgres_check_constraint("visibility = current_setting('app.visibility')") is None


def test_postgres_check_normalization_handles_postgres17_literal_cast_chains_fail_closed():
    table = sa.Table('visibility_test', sa.MetaData(), sa.Column('visibility', sa.String(20)))
    expected = _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint("visibility IN ('PUBLIC', 'MEMBERS')"), table,
    )
    reflected = _normalize_postgres_check_for_table(_canonical_postgres_check_constraint(
        "visibility::text = ANY (ARRAY['PUBLIC'::character varying::text, "
        "'MEMBERS'::character varying::text])"
    ), table)
    assert reflected == expected
    weakened = _normalize_postgres_check_for_table(_canonical_postgres_check_constraint(
        "visibility::text = ANY (ARRAY['PUBLIC'::character varying::text])"
    ), table)
    assert weakened != expected


def test_postgres_check_normalization_handles_direct_upload_or_branches_fail_closed():
    table = sa.Table(
        'direct_upload_session', sa.MetaData(),
        sa.Column('kind', sa.String(20)), sa.Column('song_id', sa.Integer()),
        sa.Column('rehearsal_id', sa.Integer()), sa.Column('member_id', sa.Integer()),
    )
    expected = _normalize_postgres_check_for_table(_canonical_postgres_check_constraint(
        "(kind = 'media' AND song_id IS NOT NULL AND member_id IS NULL) OR "
        "(kind = 'personal_log' AND song_id IS NULL AND rehearsal_id IS NULL AND member_id IS NOT NULL)", allow_or=True
    ), table)
    reflected = _normalize_postgres_check_for_table(_canonical_postgres_check_constraint(
        "((kind)::text = 'media'::text AND song_id IS NOT NULL AND member_id IS NULL) OR "
        "((kind)::text = 'personal_log'::text AND song_id IS NULL AND rehearsal_id IS NULL AND member_id IS NOT NULL)",
        allow_or=True,
    ), table)
    assert reflected == expected
    weakened = _normalize_postgres_check_for_table(_canonical_postgres_check_constraint(
        "(kind = 'media' AND song_id IS NOT NULL) OR "
        "(kind = 'personal_log' AND song_id IS NULL AND rehearsal_id IS NULL AND member_id IS NOT NULL)", allow_or=True
    ), table)
    assert weakened != expected


def test_postgres_direct_upload_or_grammar_is_limited_to_the_named_target_check():
    table = sa.Table(
        'direct_upload_session', sa.MetaData(),
        sa.Column('kind', sa.String(20)), sa.Column('song_id', sa.Integer()),
    )

    class Inspector:
        def get_check_constraints(self, table_name):
            assert table_name == 'direct_upload_session'
            return [{
                'name': 'ck_direct_upload_session_kind',
                'sqltext': "kind = 'media' OR song_id IS NOT NULL",
            }]

    reflected = _reflected_check_constraints(
        Inspector(), 'direct_upload_session', postgresql.dialect(), table,
    )
    assert reflected == Counter({('ck_direct_upload_session_kind', None): 1})


def test_postgres_numeric_check_normalization_is_structured_and_fail_closed():
    table = sa.Table('quota_compare', sa.MetaData(), sa.Column('storage_quota_bytes', sa.BigInteger))
    expected = _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint('storage_quota_bytes > 0'), table,
    )
    assert expected == _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint('(((storage_quota_bytes)::bigint) > (0)::bigint)'), table,
    )
    for invalid in ('storage_quota_bytes > 1', 'other_column > 0'):
        assert _normalize_postgres_check_for_table(_canonical_postgres_check_constraint(invalid), table) != expected
    for invalid in ('storage_quota_bytes >= 0', 'storage_quota_bytes > 0 OR storage_quota_bytes > 1',
                    'storage_quota_bytes > random()'):
        assert _canonical_postgres_check_constraint(invalid) is None
    metadata = sa.MetaData()
    table = sa.Table('quota', metadata, sa.Column('storage_quota_bytes', sa.BigInteger),
                     sa.CheckConstraint('storage_quota_bytes > 0', name='ck_quota'))
    assert _expected_check_constraints(table, postgresql.dialect())
    unsupported = sa.Table('unsupported', sa.MetaData(), sa.Column('value', sa.Integer),
                           sa.CheckConstraint('value IS NOT NULL', name='ck_unsupported'))
    with pytest.raises(Exception, match='outside the audited grammar'):
        _expected_check_constraints(unsupported, postgresql.dialect())


def test_postgres_check_casts_must_preserve_the_metadata_column_type():
    bigint = sa.Table('bigint_check', sa.MetaData(), sa.Column('storage_quota_bytes', sa.BigInteger))
    valid = _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint('((storage_quota_bytes)::numeric > (0)::numeric)'), bigint,
    )
    assert valid is not None
    assert _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint('((storage_quota_bytes)::integer > (0)::integer)'), bigint,
    ) is None
    varchar = sa.Table('varchar_check', sa.MetaData(), sa.Column('visibility', sa.String(20)))
    assert _normalize_postgres_check_for_table(
        _canonical_postgres_check_constraint("(visibility)::character(20) = 'PUBLIC'::character(20)"), varchar,
    ) is None


def test_postgres_serial_catalog_proof_requires_public_exact_owned_sequence():
    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalar_one(self):
            return self.value

    class Connection:
        def __init__(self, serial_sequence, ownership):
            self.serial_sequence = serial_sequence
            self.ownership = ownership
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))
            if 'pg_get_serial_sequence' in str(statement):
                return Result(self.serial_sequence)
            return Result(self.ownership)

    connection = Connection('public.audit_event_id_seq', True)
    assert _postgres_serial_sequence_is_owned(connection, 'audit_event', 'id', 'audit_event_id_seq')
    assert len(connection.calls) == 2
    assert not _postgres_serial_sequence_is_owned(
        Connection('other.audit_event_id_seq', True), 'audit_event', 'id', 'audit_event_id_seq',
    )


def test_backing_constraint_indexes_are_filtered_without_hiding_real_indexes():
    class Inspector:
        def get_indexes(self, table_name):
            return [
                {'name': 'uq_backing', 'column_names': ['email'], 'unique': True, 'duplicates_constraint': 'uq_email'},
                {'name': 'uq_partial', 'column_names': ['band_id'], 'unique': True,
                 'dialect_options': {'postgresql_where': "role = 'owner'"}},
            ]

    assert _reflected_indexes(Inspector(), 'band_membership') == Counter({
        ('uq_partial', ('band_id',), True): 1,
    })


def test_postgres_audit_trigger_verifier_checks_catalog_oid_contract_and_function_body():
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class Connection:
        dialect = type('Dialect', (), {'name': 'postgresql'})()

        def __init__(self, trigger_rows):
            self.trigger_rows = trigger_rows

        def execute(self, statement):
            assert 'pg_trigger' in str(statement)
            return Result(self.trigger_rows)

    triggers = [
        ('audit_event_no_update', 19, 'O', None, '', 91, 91, 'public', 'audit_event_append_only', '', 'trigger', 'plpgsql',
         "BEGIN RAISE EXCEPTION 'audit_event is append-only'; END;"),
        ('audit_event_no_delete', 11, 'O', None, (), 91, 91, 'public', 'audit_event_append_only', '', 'trigger', 'plpgsql',
         "BEGIN RAISE EXCEPTION 'audit_event is append-only'; END;"),
    ]
    assert _audit_trigger_differences(Connection(triggers)) == []
    # The catalog query is bound to public.audit_event, so an identically named
    # table in another schema never supplies a valid trigger row.
    assert _audit_trigger_differences(Connection([]))
    disabled = [list(row) for row in triggers]
    disabled[0][2] = 'D'
    assert _audit_trigger_differences(Connection([tuple(row) for row in disabled]))
    other_schema = [list(row) for row in triggers]
    other_schema[0][7] = 'other'
    assert _audit_trigger_differences(Connection([tuple(row) for row in other_schema]))
    wrong_oid = [list(row) for row in triggers]
    wrong_oid[0][5] = 123
    assert _audit_trigger_differences(Connection([tuple(row) for row in wrong_oid]))
    after = [list(row) for row in triggers]
    after[0][1] = 17  # ROW | UPDATE, but AFTER rather than BEFORE.
    assert _audit_trigger_differences(Connection([tuple(row) for row in after]))
    conditional = [list(row) for row in triggers]
    conditional[0][12] = "BEGIN IF false THEN RAISE EXCEPTION 'audit_event is append-only'; END IF; RETURN NEW; END;"
    assert _audit_trigger_differences(Connection([tuple(row) for row in conditional]))
    when_false = [list(row) for row in triggers]
    when_false[0][3] = '({OPEXPR :opno 91 :args (...)})'
    assert _audit_trigger_differences(Connection([tuple(row) for row in when_false]))
    update_of_one = [list(row) for row in triggers]
    update_of_one[0][4] = '3'
    assert _audit_trigger_differences(Connection([tuple(row) for row in update_of_one]))
    update_of_many = [list(row) for row in triggers]
    update_of_many[0][4] = '3 7'
    assert _audit_trigger_differences(Connection([tuple(row) for row in update_of_many]))
    assert _audit_trigger_differences(Connection(triggers + [
        ('audit_event_no_statement', 18, 'O', None, '', 91, 91, 'public', 'audit_event_append_only', '', 'trigger', 'plpgsql',
         "BEGIN RAISE EXCEPTION 'audit_event is append-only'; END;"),
    ]))


@pytest.mark.parametrize(
    ('expression', 'expected'),
    [
        ("(((role)::text = 'owner'::text) AND ((status)::text = 'active'::text))",
         frozenset({('role', 'owner'), ('status', 'active')})),
        ("((status)::text = 'pending'::text)", frozenset({('status', 'pending')})),
        ("((status)::text = 'active'::text) AND ((role)::text = 'owner'::text)",
         frozenset({('role', 'owner'), ('status', 'active')})),
        ("(((role))::text = (('owner')::text)) AND (((status)::text) = ('active'::text))",
         frozenset({('role', 'owner'), ('status', 'active')})),
        ("((status)::text='pending'::text)", frozenset({('status', 'pending')})),
        ("((state)::text = 'active'::text)", frozenset({('state', 'active')})),
        ("((audit_event.event_type)::text = 'upload_policy_confirmed'::text)",
         frozenset({('event_type', 'upload_policy_confirmed')})),
        ("state = 'released'", None),
        ("event_type = 'active'", None),
        ("state = 'active' OR state = 'released'", None),
        ("state = 'active' AND status = 'pending'", None),
        ("(((gallery_image.is_featured IS TRUE)))", ('is_featured', 'is_true')),
        ("(invalidated_at IS NULL) AND (redeemed_at IS NULL)",
         frozenset({('invalidated_at', 'is_null'), ('redeemed_at', 'is_null')})),
        ("(role = 'owner')", None),
        ("(role = 'owner') OR (status = 'active')", None),
        ("(role = 'owner') AND (status = 'rejected')", None),
        ("(role)::varchar = 'owner'::varchar", None),
        ("(role)::text = 'owner'::text AND (status)::text = 'pending'::text", None),
        ("(status = CURRENT_SETTING('role'))", None),
        ("is_featured", None),
        ("is_featured IS NOT FALSE", None),
        ("is_featured = TRUE", None),
        ("invalidated_at IS NULL", None),
        ("invalidated_at IS NULL OR redeemed_at IS NULL", None),
    ],
)
def test_postgres_partial_predicate_parser_is_semantic_and_fail_closed(expression, expected):
    assert _canonical_postgres_partial_predicate(expression) == expected


class _PostgresIndexesConnection:
    """Minimal pg_indexes/pg_get_expr result used by the preflight verifier."""

    dialect = type('Dialect', (), {'name': 'postgresql'})()

    def __init__(self, owner_predicate, request_predicate, gallery_predicate='(is_featured IS TRUE)',
                 featured_media_predicate='(is_featured IS TRUE)',
                 restriction_predicate="((state)::text = 'active'::text)",
                 upload_predicate="((event_type)::text = 'upload_policy_confirmed'::text)"):
        self.rows = [
            ('band_membership', 'uq_band_membership_active_owner', owner_predicate),
            ('band_creation_request', 'uq_band_creation_request_pending_user_slug', request_predicate),
            ('gallery_image', 'uq_gallery_image_featured_band', gallery_predicate),
            ('media', 'uq_media_featured_band_song', featured_media_predicate),
            ('media_vote', 'uq_media_vote_membership', '(created_by_membership_id IS NOT NULL)'),
            ('song_suggestion_vote', 'uq_song_suggestion_vote_user', '(voter_user_id IS NOT NULL)'),
            ('legacy_band_claim_token', 'uq_legacy_band_claim_outstanding',
             '(invalidated_at IS NULL) AND (redeemed_at IS NULL)'),
            ('content_restriction', 'ix_content_restriction_active_target', restriction_predicate),
            ('audit_event', 'ix_audit_event_upload_policy_target', upload_predicate),
        ]

    def execute(self, statement):
        assert 'pg_indexes' in str(statement)
        return self.rows


@pytest.mark.parametrize(
    ('owner_predicate', 'request_predicate', 'valid'),
    [
        ("(((role)::text = 'owner'::text) AND ((status)::text = 'active'::text))",
         "((status)::text = 'pending'::text)", True),
        ("((role)::text = 'owner'::text)", "((status)::text = 'pending'::text)", False),
        ("((role)::text = 'owner'::text) OR ((status)::text = 'active'::text)",
         "((status)::text = 'pending'::text)", False),
        ("((role)::text = 'owner'::text) AND ((status)::text = 'pending'::text)",
         "((status)::text = 'pending'::text)", False),
    ],
)
def test_postgres_pg_indexes_predicates_are_verified_semantically(
    tmp_path, owner_predicate, request_predicate, valid,
):
    app = _migration_app(tmp_path / 'postgres-shape.db')
    try:
        with app.app_context():
            differences = _partial_index_differences(
                _PostgresIndexesConnection(owner_predicate, request_predicate), db.metadata,
            )
        assert bool(differences) is not valid
    finally:
        _dispose(app)


@pytest.mark.parametrize(
    ('gallery_predicate', 'valid'),
    [
        ('((is_featured IS TRUE))', True),
        (None, False),
        ('is_featured', False),
        ('is_featured IS NOT FALSE', False),
        ('is_featured = TRUE', False),
    ],
)
def test_postgres_featured_gallery_partial_index_predicate_is_exact(
        tmp_path, gallery_predicate, valid):
    app = _migration_app(tmp_path / 'postgres-gallery-predicate.db')
    try:
        with app.app_context():
            connection = _PostgresIndexesConnection(
                "((role)::text = 'owner'::text) AND ((status)::text = 'active'::text)",
                "((status)::text = 'pending'::text)",
                gallery_predicate,
            )
            differences = _partial_index_differences(connection, db.metadata)
        gallery_differences = [item for item in differences if item.startswith('gallery_image.')]
        assert bool(gallery_differences) is not valid
    finally:
        _dispose(app)


@pytest.mark.parametrize(
    ('restriction_predicate', 'upload_predicate', 'invalid_index'),
    [
        ("((state)::text = 'released'::text)",
         "((event_type)::text = 'upload_policy_confirmed'::text)",
         'content_restriction.ix_content_restriction_active_target'),
        ("state = 'active' OR state = 'released'",
         "((event_type)::text = 'upload_policy_confirmed'::text)",
         'content_restriction.ix_content_restriction_active_target'),
        ("((state)::text = 'active'::text)",
         "((event_type)::text = 'upload_policy_rejected'::text)",
         'audit_event.ix_audit_event_upload_policy_target'),
        ("((state)::text = 'active'::text)",
         "event_type = 'upload_policy_confirmed' OR event_type = 'upload_policy_rejected'",
         'audit_event.ix_audit_event_upload_policy_target'),
    ],
)
def test_postgres_new_policy_partial_indexes_reject_changed_predicates(
        tmp_path, restriction_predicate, upload_predicate, invalid_index):
    app = _migration_app(tmp_path / 'postgres-policy-predicate.db')
    try:
        with app.app_context():
            connection = _PostgresIndexesConnection(
                "((role)::text = 'owner'::text) AND ((status)::text = 'active'::text)",
                "((status)::text = 'pending'::text)",
                restriction_predicate=restriction_predicate,
                upload_predicate=upload_predicate,
            )
            differences = _partial_index_differences(connection, db.metadata)
        assert any(item.startswith(invalid_index) for item in differences)
    finally:
        _dispose(app)


def test_postgres_suggestion_vote_partial_index_contract_is_exact(tmp_path):
    app = _migration_app(tmp_path / 'postgres-suggestion-vote-predicate.db')
    try:
        with app.app_context():
            index = next(item for item in db.metadata.tables['song_suggestion_vote'].indexes
                         if item.name == 'uq_song_suggestion_vote_user')
            assert tuple(column.name for column in index.columns) == ('suggestion_id', 'voter_user_id')
            assert _canonical_postgres_partial_predicate(
                str(index.dialect_options['postgresql']['where'])
            ) == ('voter_user_id', 'is_not_null')
            differences = _partial_index_differences(
                _PostgresIndexesConnection(
                    "((role)::text = 'owner'::text) AND ((status)::text = 'active'::text)",
                    "((status)::text = 'pending'::text)",
                ), db.metadata,
            )
            assert not [item for item in differences if item.startswith('song_suggestion_vote.')]
    finally:
        _dispose(app)


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
        # v1 remains healthy; the Phase 3 blueprint fails closed without
        # touching absent tables instead of returning an operational 500.
        unavailable = runtime.test_client().get('/api/v2/public/bands')
        assert unavailable.status_code == 503
        assert unavailable.get_json()['error'] == 'band_schema_unavailable'
    finally:
        _dispose(runtime)
    assert database_path.read_bytes() == before_runtime_boot

    upgraded = _upgrade_through_read_scope_contract(database_path)
    assert upgraded.exit_code == 0, upgraded.output
    repeated = _runner_command(database_path, 'upgrade')
    assert repeated.exit_code == 0, repeated.output
    runtime_after_upgrade = create_app(_runtime_config(database_path), start_worker=False)
    try:
        with runtime_after_upgrade.app_context():
            assert {'app_user', 'auth_bootstrap_state'}.issubset(inspect(db.engine).get_table_names())
            assert _version(runtime_after_upgrade) == migration_head_revision()
            assert runtime_after_upgrade.config['BAND_API_SCHEMA_READY'] is True
            with db.engine.begin() as connection:
                connection.execute(text("INSERT INTO audit_event (event_type, created_at) VALUES ('test', CURRENT_TIMESTAMP)"))
                with pytest.raises(Exception):
                    connection.execute(text("UPDATE audit_event SET event_type = 'changed'"))
                with pytest.raises(Exception):
                    connection.execute(text("DELETE FROM audit_event"))
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
    assert _upgrade_through_read_scope_contract(clean_path).exit_code == 0
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
