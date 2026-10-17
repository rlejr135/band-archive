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
from sqlalchemy import Boolean, CheckConstraint, Integer, BigInteger, SmallInteger, MetaData, inspect, text
from sqlalchemy.dialects import postgresql

from extensions import db


MIGRATIONS_DIRECTORY = Path(__file__).with_name('migrations')
BASELINE_REVISION = '20260914_01'
AUTH_REVISION = '20260914_02'
PHASE3_REVISION = '20260915_03'
PHASE3_HARDENED_REVISION = '20260916_04'
PHASE4_REVISION = '20260916_05'
PHASE6_REVISION = '20260920_06'
PHASE7_REVISION = '20260927_07'
PHASE8_REVISION = '20261002_08'
PHASE9_REVISION = '20261002_09'
PHASE10_REVISION = '20261003_10'
PHASE11_REVISION = '20261004_11'
PHASE12_REVISION = '20261006_12'
PHASE13_REVISION = '20261010_13'
PHASE14_REVISION = '20261014_14'
PHASE15_REVISION = '20261016_15'
PHASE16_REVISION = '20261017_16'
PHASE17_REVISION = '20261017_17'
ALEMBIC_VERSION_TABLE = 'alembic_version'
DATA_API_MANAGED_TABLES_PHASE13 = (
    'alembic_version', 'announcement', 'app_user', 'audit_event', 'auth_bootstrap_state', 'band',
    'band_creation_request', 'band_invite_code', 'band_membership', 'comment', 'direct_upload_session',
    'gallery_image', 'legacy_band_claim_token', 'media', 'media_vote', 'member', 'multipart_upload_part',
    'multipart_upload_session', 'personal_log', 'personal_log_multipart_upload_part',
    'personal_log_multipart_upload_session', 'rehearsal', 'rehearsal_songs', 'song', 'song_suggestion',
    'song_suggestion_vote', 'song_vote', 'tenant_backfill_state',
)
DATA_API_MANAGED_TABLES_PHASE14 = tuple(sorted((*DATA_API_MANAGED_TABLES_PHASE13, 'band_request_email_outbox')))
DATA_API_MANAGED_TABLES_PHASE15 = tuple(sorted((*DATA_API_MANAGED_TABLES_PHASE14, 'r2_deletion_task')))
DATA_API_MANAGED_TABLES_PHASE16 = tuple(sorted((*DATA_API_MANAGED_TABLES_PHASE15, 'policy_acceptance_receipt')))
AVAILABILITY_TABLES = ('band_availability_day', 'band_availability_state', 'band_personal_schedule_note')
DATA_API_MANAGED_TABLES = tuple(sorted((*DATA_API_MANAGED_TABLES_PHASE16, *AVAILABILITY_TABLES)))
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
    _restore_pre_phase7_content_writes(metadata)
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
    _remove_future_policy_acceptance(metadata)
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
    _remove_future_policy_acceptance(metadata)
    _restore_pre_phase4_band(metadata)
    return metadata


def _remove_future_email_outbox(metadata):
    """Keep pre-revision-14 snapshots independent of later ORM additions."""
    if 'band_request_email_outbox' in metadata.tables:
        metadata.remove(metadata.tables['band_request_email_outbox'])
    return metadata


def _remove_future_r2_deletion_task(metadata):
    """Keep all immutable snapshots before revision 15 structurally exact."""
    if 'r2_deletion_task' in metadata.tables:
        metadata.remove(metadata.tables['r2_deletion_task'])
    return metadata


def _remove_future_policy_acceptance(metadata):
    """Keep all immutable snapshots before revision 16 structurally exact."""
    table = metadata.tables.get('band_membership')
    if table is not None and table.c.get('join_introduction') is not None:
        table._columns.remove(table.c.join_introduction)
    if 'policy_acceptance_receipt' in metadata.tables:
        metadata.remove(metadata.tables['policy_acceptance_receipt'])
    for name in AVAILABILITY_TABLES:
        if name in metadata.tables:
            metadata.remove(metadata.tables[name])
    return metadata


def phase4_metadata():
    """Exact revision-05 snapshot before Phase 6 contracts tenant reads."""
    metadata = MetaData()
    for table in db.metadata.tables.values():
        table.to_metadata(metadata)
    _remove_future_email_outbox(metadata)
    _remove_future_r2_deletion_task(metadata)
    _remove_future_policy_acceptance(metadata)
    _restore_pre_phase7_content_writes(metadata)
    _restore_pre_phase8_suggestion_votes(metadata)
    _restore_pre_phase9_direct_upload_sessions(metadata)
    _restore_pre_phase12_legacy_claim_state(metadata)
    for table_name in (
        'song', 'rehearsal', 'media', 'member', 'personal_log', 'gallery_image',
        'announcement', 'song_suggestion', 'comment', 'song_vote', 'media_vote',
        'multipart_upload_session', 'personal_log_multipart_upload_session',
    ):
        metadata.tables[table_name].c.band_id.nullable = True
    for table_name in ('song', 'rehearsal', 'media', 'personal_log', 'gallery_image',
                       'announcement', 'song_suggestion'):
        metadata.tables[table_name].c.visibility.nullable = True
    for table_name, constraint_name in (('announcement', 'uq_announcement_band'),):
        table = metadata.tables[table_name]
        constraint = next((item for item in table.constraints if item.name == constraint_name), None)
        if constraint is not None:
            table.constraints.remove(constraint)
    read_indexes = {
        'ix_song_band_visibility_id', 'ix_rehearsal_band_visibility_date_id',
        'ix_media_band_song_visibility_score_id', 'ix_media_band_rehearsal_visibility_score_id',
        'ix_personal_log_band_member_visibility_created_id', 'ix_gallery_image_band_visibility_created_id',
        'ix_comment_band_media_parent_created_id', 'ix_comment_band_personal_log_parent_created_id',
        'uq_gallery_image_featured_band',
    }
    for table in metadata.tables.values():
        for index in list(table.indexes):
            if index.name in read_indexes:
                table.indexes.remove(index)
    return metadata


def phase6_metadata():
    """Exact revision-06 snapshot before the account write contract."""
    metadata = MetaData()
    for table in db.metadata.tables.values():
        table.to_metadata(metadata)
    _remove_future_email_outbox(metadata)
    _remove_future_r2_deletion_task(metadata)
    _remove_future_policy_acceptance(metadata)
    _restore_pre_phase7_content_writes(metadata)
    _restore_pre_phase8_suggestion_votes(metadata)
    _restore_pre_phase9_direct_upload_sessions(metadata)
    _restore_pre_phase12_legacy_claim_state(metadata)
    return metadata


def phase7_metadata():
    """Exact revision-07 snapshot before account suggestion votes."""
    metadata = MetaData()
    for table in db.metadata.tables.values():
        table.to_metadata(metadata)
    _remove_future_email_outbox(metadata)
    _remove_future_r2_deletion_task(metadata)
    _remove_future_policy_acceptance(metadata)
    _restore_pre_phase8_suggestion_votes(metadata)
    _restore_pre_phase9_direct_upload_sessions(metadata)
    _restore_pre_phase12_legacy_claim_state(metadata)
    return metadata


def phase8_metadata():
    """Exact revision-08 snapshot before direct-upload reservations existed."""
    metadata = MetaData()
    for table in db.metadata.tables.values():
        table.to_metadata(metadata)
    _remove_future_email_outbox(metadata)
    _remove_future_r2_deletion_task(metadata)
    _remove_future_policy_acceptance(metadata)
    _restore_pre_phase9_direct_upload_sessions(metadata)
    _restore_pre_phase12_legacy_claim_state(metadata)
    return metadata


def phase9_metadata():
    """Exact revision-09 snapshot before expiring became a cleanup claim."""
    metadata = MetaData()
    for table in db.metadata.tables.values():
        table.to_metadata(metadata)
    _remove_future_email_outbox(metadata)
    _remove_future_r2_deletion_task(metadata)
    _remove_future_policy_acceptance(metadata)
    _restore_pre_phase11_direct_gallery_state(metadata)
    _restore_pre_phase10_direct_expiry_state(metadata)
    _restore_pre_phase12_legacy_claim_state(metadata)
    return metadata


def phase10_metadata():
    """Exact revision-10 snapshot before direct gallery reservations."""
    metadata = MetaData()
    for table in db.metadata.tables.values():
        table.to_metadata(metadata)
    _remove_future_email_outbox(metadata)
    _remove_future_r2_deletion_task(metadata)
    _remove_future_policy_acceptance(metadata)
    _restore_pre_phase11_direct_gallery_state(metadata)
    _restore_pre_phase12_legacy_claim_state(metadata)
    return metadata


def phase11_metadata():
    """Exact revision-11 snapshot before ownership-claim capabilities."""
    metadata = MetaData()
    for table in db.metadata.tables.values():
        table.to_metadata(metadata)
    _remove_future_email_outbox(metadata)
    _remove_future_r2_deletion_task(metadata)
    _remove_future_policy_acceptance(metadata)
    _restore_pre_phase12_legacy_claim_state(metadata)
    return metadata


def phase12_metadata():
    """Exact revision-12 structural snapshot before Data API hardening.

    Revision 13 changes PostgreSQL ACL/RLS state only.  Keep a dedicated
    structural copy nevertheless: future model changes must not invalidate a
    legitimate v12 database during upgrade preflight.
    """
    # Build from the preceding historical snapshot, then add only revision
    # 12's table.  Do not make the v12 contract a bare alias of current ORM
    # metadata; future schema changes must update this builder deliberately.
    metadata = phase11_metadata()
    db.metadata.tables['legacy_band_claim_token'].to_metadata(metadata)
    return metadata


def phase14_metadata():
    """Exact immutable revision-14 snapshot, before deletion tasks existed."""
    metadata = phase12_metadata()
    db.metadata.tables['band_request_email_outbox'].to_metadata(metadata)
    return metadata


def phase15_metadata():
    """Exact revision-15 structural snapshot with the private deletion queue."""
    metadata = phase14_metadata()
    db.metadata.tables['r2_deletion_task'].to_metadata(metadata)
    return metadata


def phase16_metadata():
    """Exact revision-16 snapshot with join introductions and receipts."""
    metadata = phase15_metadata()
    membership = metadata.tables['band_membership']
    membership.append_column(db.metadata.tables['band_membership'].c.join_introduction._copy())
    db.metadata.tables['policy_acceptance_receipt'].to_metadata(metadata)
    return metadata


def phase17_metadata():
    """Private band-scoped availability tables; historical snapshots stay exact."""
    metadata = phase16_metadata()
    for name in AVAILABILITY_TABLES:
        db.metadata.tables[name].to_metadata(metadata)
    return metadata


def _restore_pre_phase7_content_writes(metadata):
    """Remove v07-only nullability/indexes from older immutable snapshots."""
    comment = metadata.tables.get('comment')
    if comment is not None and comment.c.get('password_hash') is not None:
        comment.c.password_hash.nullable = False
    media_vote = metadata.tables.get('media_vote')
    if media_vote is not None:
        index = next((item for item in media_vote.indexes if item.name == 'uq_media_vote_membership'), None)
        if index is not None:
            media_vote.indexes.remove(index)


def _restore_pre_phase8_suggestion_votes(metadata):
    """Remove the v08 table from earlier immutable schema snapshots."""
    table = metadata.tables.get('song_suggestion_vote')
    if table is not None:
        metadata.remove(table)


def _restore_pre_phase9_direct_upload_sessions(metadata):
    """Remove the v09 table from immutable snapshots of earlier revisions."""
    table = metadata.tables.get('direct_upload_session')
    if table is not None:
        metadata.remove(table)


def _restore_pre_phase10_direct_expiry_state(metadata):
    """Restore the v09 direct-session status constraint in older snapshots."""
    table = metadata.tables.get('direct_upload_session')
    if table is None:
        return
    constraint = next((item for item in table.constraints
                       if item.name == 'ck_direct_upload_session_status'), None)
    if constraint is not None:
        table.constraints.remove(constraint)
    table.append_constraint(CheckConstraint(
        "status IN ('initiated', 'completing', 'completed', 'expired', 'failed')",
        name='ck_direct_upload_session_status',
    ))


def _restore_pre_phase11_direct_gallery_state(metadata):
    """Undo gallery direct-upload columns and checks for the v10 snapshot."""
    table = metadata.tables.get('direct_upload_session')
    if table is None:
        return
    gallery_column = table.c.get('result_gallery_image_id')
    if gallery_column is not None:
        for constraint in list(table.constraints):
            if gallery_column in constraint.columns:
                table.constraints.remove(constraint)
        table._columns.remove(gallery_column)
    for constraint in list(table.constraints):
        if constraint.name in {'ck_direct_upload_session_kind', 'ck_direct_upload_session_target'}:
            table.constraints.remove(constraint)
    table.append_constraint(CheckConstraint(
        "kind IN ('media', 'personal_log')",
        name='ck_direct_upload_session_kind',
    ))
    table.append_constraint(CheckConstraint(
        "(kind = 'media' AND song_id IS NOT NULL AND member_id IS NULL) OR "
        "(kind = 'personal_log' AND song_id IS NULL AND rehearsal_id IS NULL AND member_id IS NOT NULL)",
        name='ck_direct_upload_session_target',
    ))


def _restore_pre_phase12_legacy_claim_state(metadata):
    """Remove the one-time legacy ownership capability before revision 12."""
    table = metadata.tables.get('legacy_band_claim_token')
    if table is not None:
        metadata.remove(table)


def _restore_pre_phase4_band(metadata):
    """Remove the ownerless-import exception from immutable v03/v04 views."""
    band = metadata.tables['band']
    band.c.created_by_user_id.nullable = False
    band._columns.remove(band.c.is_legacy_import)


def _normalize_type(column_type, dialect):
    return re.sub(r'\s+', '', column_type.compile(dialect=dialect)).upper()


def types_are_equivalent(expected, actual, dialect):
    """Compare reflected types while retaining dialect-specific safety rules."""
    expected_rendered = _normalize_type(expected, dialect)
    actual_rendered = _normalize_type(actual, dialect)
    if expected_rendered == actual_rendered:
        return True
    if dialect.name == 'postgresql':
        # PostgreSQL reflects an unqualified SQLAlchemy Float as DOUBLE
        # PRECISION.  This is intentionally directional and narrow: REAL,
        # FLOAT(24), and every other explicit precision remain drift.
        return (
            expected_rendered == 'FLOAT'
            and getattr(expected, 'precision', None) is None
            and actual_rendered == 'DOUBLEPRECISION'
            and getattr(actual, 'precision', None) in (None, 53)
        )
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


def _postgres_default_cast(value, column, reflected=False):
    """Normalize one PostgreSQL reflection cast only when it is type-safe.

    PostgreSQL adds casts such as ``'active'::character varying`` and
    ``21474836480::bigint`` to reflected defaults.  Do not strip arbitrary
    casts: doing so could turn a changed type or an expression into a match.
    """
    if value is None:
        return None
    raw = _strip_sql_parentheses(str(value).strip())
    match = re.fullmatch(r'(.+?)\s*::\s*([a-z ]+(?:\[\])?)', raw, flags=re.IGNORECASE)
    if not match:
        expression = raw
        cast = None
    else:
        expression = _strip_sql_parentheses(match.group(1))
        cast = re.sub(r'\s+', ' ', match.group(2).strip().casefold())
    # Do not depend on metadata.bind in normal use; the isinstance checks are
    # stable across SQLAlchemy's PostgreSQL type subclasses.
    if isinstance(column.type, Boolean):
        allowed = {'boolean', 'bool'}
    elif isinstance(column.type, (BigInteger,)):
        allowed = {'bigint', 'int8'}
    elif isinstance(column.type, (Integer, SmallInteger)):
        allowed = {'integer', 'int', 'int4', 'smallint', 'int2'}
    else:
        allowed = {'character varying', 'varchar', 'text', 'character', 'char'}
    if cast is not None and (cast not in allowed or '::' in expression):
        # A non-safe cast is retained with a marker that cannot compare equal
        # to a metadata literal.
        return f'__unsafe_postgres_cast__:{raw}'
    # Metadata's string server_default shorthand is unquoted, but a reflected
    # PostgreSQL text literal must be quoted.  This prevents a bare keyword or
    # expression from being mistaken for that shorthand.
    if reflected and not isinstance(column.type, (Boolean, Integer, BigInteger, SmallInteger)) and not re.fullmatch(
            r"'(?:''|[^'])*'", expression):
        return f'__unsafe_postgres_text_default__:{raw}'
    return expression


def _postgres_boolean_default(value):
    normalized = _normalize_default(value)
    if normalized in {'1', 'true'}:
        return True
    if normalized in {'0', 'false'}:
        return False
    return None


def _postgres_automatic_integer_primary_key(column):
    """Return whether metadata actually requests a generated integer default.

    SQLAlchemy exposes ``autoincrement='auto'`` on many primary-key columns,
    including each member of a composite key.  Only a single-column integer PK
    selects that implicit behavior.  A composite key needs an explicit True.
    """
    if not isinstance(column.type, (Integer, BigInteger, SmallInteger)):
        return False
    if not column.primary_key or column.server_default is not None:
        return False
    if column.autoincrement is True:
        return True
    return column.autoincrement == 'auto' and len(column.table.primary_key.columns) == 1


def _postgres_serial_sequence_is_owned(connection, table_name, column_name, sequence_name):
    """Prove that the column default and sequence have the expected PG ownership.

    The reflected ``nextval`` text deliberately is not enough: an unqualified
    regclass can look like the expected sequence while resolving through a
    different search path.  This catalog proof binds the exact public sequence
    to both the column default and the sequence ownership dependency.
    """
    expected_reference = f'public.{sequence_name}'
    serial_sequence = connection.execute(text("""
        SELECT pg_get_serial_sequence(:table_name, :column_name)
    """), {'table_name': f'public.{table_name}', 'column_name': column_name}).scalar_one_or_none()
    if serial_sequence != expected_reference:
        return False
    return bool(connection.execute(text("""
        SELECT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            JOIN pg_class AS table_class ON table_class.oid = attribute.attrelid
            JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
            JOIN pg_attrdef AS attribute_default
              ON attribute_default.adrelid = attribute.attrelid
             AND attribute_default.adnum = attribute.attnum
            JOIN pg_class AS sequence_class
              ON sequence_class.relname = :sequence_name
            JOIN pg_namespace AS sequence_namespace
              ON sequence_namespace.oid = sequence_class.relnamespace
             AND sequence_namespace.nspname = 'public'
            JOIN pg_depend AS default_dependency
              ON default_dependency.classid = 'pg_attrdef'::regclass
             AND default_dependency.objid = attribute_default.oid
             AND default_dependency.refobjid = sequence_class.oid
            JOIN pg_depend AS ownership_dependency
              ON ownership_dependency.classid = 'pg_class'::regclass
             AND ownership_dependency.objid = sequence_class.oid
             AND ownership_dependency.refobjid = attribute.attrelid
             AND ownership_dependency.refobjsubid = attribute.attnum
             AND ownership_dependency.deptype = 'a'
            WHERE table_namespace.nspname = 'public'
              AND table_class.relname = :table_name
              AND attribute.attname = :column_name
        )
    """), {
        'table_name': table_name,
        'column_name': column_name,
        'sequence_name': sequence_name,
    }).scalar_one())


def _postgres_owned_sequence_default(value, table_name, column, connection=None, sequence_resolver=None):
    """Recognize only a catalog-proven serial sequence for this PK column."""
    if not _postgres_automatic_integer_primary_key(column):
        return False
    raw = _strip_sql_parentheses(str(value).strip()) if value is not None else ''
    match = re.fullmatch(
        r"nextval\(\s*'(?P<sequence>[^']+)'\s*::\s*regclass\s*\)", raw,
        flags=re.IGNORECASE,
    )
    if not match:
        return False
    # Do not tolerate qualification or quoted identifiers in the reflected
    # expression. The catalog proof below resolves the unqualified regclass to
    # the exact public relation created by our migrations.
    sequence_name = f'{table_name}_{column.name}_seq'
    if match.group('sequence') != sequence_name:
        return False
    resolver = sequence_resolver or _postgres_serial_sequence_is_owned
    return bool(resolver(connection, table_name, column.name, sequence_name))


def _defaults_are_equivalent(expected_default, actual_default, column, table_name, dialect,
                             connection=None, sequence_resolver=None):
    """Compare defaults with PostgreSQL reflection-only normalization."""
    if dialect.name != 'postgresql':
        return _normalize_default(actual_default) == _normalize_default(expected_default)
    if expected_default is None:
        if _postgres_automatic_integer_primary_key(column):
            return _postgres_owned_sequence_default(
                actual_default, table_name, column, connection, sequence_resolver,
            )
        return actual_default is None
    expected_value = _postgres_default_cast(expected_default, column)
    actual_value = _postgres_default_cast(actual_default, column, reflected=True)
    if isinstance(column.type, Boolean):
        expected_boolean = _postgres_boolean_default(expected_value)
        actual_boolean = _postgres_boolean_default(actual_value)
        return expected_boolean is not None and expected_boolean == actual_boolean
    return _normalize_default(expected_value) == _normalize_default(actual_value)


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
            _constraint_deferrability(constraint),
        ))
    return Counter(expected)


def _reflected_foreign_keys(inspector, table_name):
    reflected = []
    for foreign_key in inspector.get_foreign_keys(table_name):
        options = foreign_key.get('options') or {}
        name = foreign_key.get('name')
        reflected.append((
            name,
            tuple(foreign_key.get('constrained_columns') or ()),
            foreign_key.get('referred_table'),
            tuple(foreign_key.get('referred_columns') or ()),
            str(options.get('ondelete') or '').upper(),
            str(options.get('onupdate') or '').upper(),
            _reflected_constraint_deferrability(options),
        ))
    return Counter(reflected)


def _expected_unique_constraints(table):
    return Counter(
        (constraint.name, tuple(column.name for column in constraint.columns),
         _constraint_deferrability(constraint))
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
        (constraint.get('name'), tuple(constraint.get('column_names') or ()),
         _reflected_constraint_deferrability(constraint.get('options') or constraint.get('dialect_options') or {}))
        for constraint in constraints
    )


def _constraint_deferrability(constraint):
    return (getattr(constraint, 'deferrable', None), getattr(constraint, 'initially', None))


def _reflected_constraint_deferrability(options):
    deferrable = options.get('deferrable')
    initially = options.get('initially')
    if isinstance(deferrable, str):
        deferrable = deferrable.casefold() == 'true'
    if isinstance(initially, str):
        initially = initially.upper()
    return deferrable, initially


def _constraints_are_equivalent(expected, actual):
    """Match anonymous metadata constraints by structure, named ones exactly."""
    remaining = list(actual.elements())
    for expected_item in expected.elements():
        expected_name, *expected_shape = expected_item
        match_index = next((
            index for index, actual_item in enumerate(remaining)
            if actual_item[1:] == tuple(expected_shape)
            and (expected_name is None or actual_item[0] == expected_name)
        ), None)
        if match_index is None:
            return False
        remaining.pop(match_index)
    return not remaining


def _expected_check_constraints(table, dialect=None):
    expected = []
    for constraint in table.constraints:
        if constraint.__class__.__name__ != 'CheckConstraint':
            continue
        if table.name == 'policy_acceptance_receipt' and constraint.name in {
            'ck_policy_receipt_terms_accepted', 'ck_policy_receipt_age_assertion',
        }:
            column = ('terms_accepted' if constraint.name == 'ck_policy_receipt_terms_accepted'
                      else 'age_14_plus')
            canonical = _canonical_policy_boolean_check(constraint.sqltext, column)
        else:
            canonical = _canonical_check_constraint(
            constraint.sqltext, dialect,
            allow_or=(table.name, constraint.name) in {
                ('direct_upload_session', 'ck_direct_upload_session_target'),
                ('r2_deletion_task', 'ck_r2_deletion_task_lease_state'),
                ('r2_deletion_task', 'ck_r2_deletion_task_manifest_state'),
            },
            allow_not_equal=(table.name, constraint.name) in {
                ('r2_deletion_task', 'ck_r2_deletion_task_lease_state'),
                ('r2_deletion_task', 'ck_r2_deletion_task_manifest_state'),
                ('policy_acceptance_receipt', 'ck_policy_receipt_terms_version'),
                ('policy_acceptance_receipt', 'ck_policy_receipt_privacy_version'),
            },
            allow_greater_equal=(table.name, constraint.name) in {
                ('r2_deletion_task', 'ck_r2_deletion_task_attempt_count'),
                ('band_availability_state', 'ck_availability_state_generation'),
            },
            )
        if (getattr(dialect, 'name', None) == 'postgresql'
                and not (isinstance(canonical, tuple) and canonical[:1] == ('policy_boolean_true',))):
            canonical = _normalize_postgres_check_for_table(canonical, table)
        if getattr(dialect, 'name', None) == 'postgresql' and canonical is None:
            raise MigrationBootstrapError(
                f'PostgreSQL CHECK metadata is outside the audited grammar for '
                f'{table.name}.{constraint.name}; refusing to stamp.'
            )
        expected.append((constraint.name, canonical))
    return Counter(expected)


def _reflected_check_constraints(inspector, table_name, dialect=None, table=None):
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
        if table_name == 'policy_acceptance_receipt' and constraint.get('name') in {
            'ck_policy_receipt_terms_accepted', 'ck_policy_receipt_age_assertion',
        }:
            column = ('terms_accepted' if constraint['name'] == 'ck_policy_receipt_terms_accepted'
                      else 'age_14_plus')
            canonical = _canonical_policy_boolean_check(constraint['sqltext'], column)
        else:
            canonical = _canonical_check_constraint(
            constraint['sqltext'], dialect,
            allow_or=(table_name, constraint.get('name')) in {
                ('direct_upload_session', 'ck_direct_upload_session_target'),
                ('r2_deletion_task', 'ck_r2_deletion_task_lease_state'),
                ('r2_deletion_task', 'ck_r2_deletion_task_manifest_state'),
            },
            allow_not_equal=(table_name, constraint.get('name')) in {
                ('r2_deletion_task', 'ck_r2_deletion_task_lease_state'),
                ('r2_deletion_task', 'ck_r2_deletion_task_manifest_state'),
                ('policy_acceptance_receipt', 'ck_policy_receipt_terms_version'),
                ('policy_acceptance_receipt', 'ck_policy_receipt_privacy_version'),
            },
            allow_greater_equal=(table_name, constraint.get('name')) in {
                ('r2_deletion_task', 'ck_r2_deletion_task_attempt_count'),
                ('band_availability_state', 'ck_availability_state_generation'),
            },
            )
        if (getattr(dialect, 'name', None) == 'postgresql' and table is not None
                and not (isinstance(canonical, tuple) and canonical[:1] == ('policy_boolean_true',))):
            canonical = _normalize_postgres_check_for_table(canonical, table)
        reflected.append((constraint.get('name'), canonical))
    return Counter(reflected)


def _canonical_policy_boolean_check(value, column):
    """Recognize only the reviewed boolean-true receipt checks."""
    if value is None:
        return None
    normalized = re.sub(r'[\s"()]', '', str(value)).casefold()
    if normalized in {f'{column}istrue', f'{column}=true'}:
        return ('policy_boolean_true', column)
    return None


_POSTGRES_CHECK_CAST = re.compile(
    r'(.+?)\s*::\s*([a-z ]+(?:\(\d+\))?(?:\[\])?)\s*$', re.IGNORECASE,
)


def _postgres_check_cast_parts(value, allow_inner_casts=False):
    value = _strip_sql_parentheses(value)
    match = _POSTGRES_CHECK_CAST.fullmatch(value)
    if not match:
        return value, None
    base = _strip_sql_parentheses(match.group(1))
    if not allow_inner_casts and '::' in base:
        return None, None
    return base, re.sub(r'\s+', '', match.group(2).casefold())


def _split_postgres_top_level(value, delimiter):
    """Split a controlled SQL list without accepting malformed quoting."""
    pieces, start, depth, quote = [], 0, 0, False
    index = 0
    while index < len(value):
        character = value[index]
        if character == "'":
            if quote and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            quote = not quote
        elif not quote and character == '(':
            depth += 1
        elif not quote and character == ')':
            depth -= 1
            if depth < 0:
                return None
        elif not quote and depth == 0 and value.startswith(delimiter, index):
            pieces.append(value[start:index].strip())
            index += len(delimiter)
            start = index
            continue
        index += 1
    if quote or depth != 0:
        return None
    pieces.append(value[start:].strip())
    return pieces if all(pieces) else None


def _postgres_check_operand(value):
    # PostgreSQL 17 may render a coerced VARCHAR literal as
    # ``'value'::character varying::text`` inside an ANY array.  Keep every
    # cast rather than discarding it: each one is checked against the metadata
    # type below, so a changed predicate cannot become an accepted cosmetic
    # variation merely by adding a cast chain.
    casts = []
    while True:
        value, cast = _postgres_check_cast_parts(value, allow_inner_casts=True)
        if value is None:
            return None
        if cast is None:
            break
        casts.append(cast)
    cast = casts[0] if len(casts) == 1 else tuple(casts) if casts else None
    if re.fullmatch(r"'(?:''|[^'])*'", value):
        return ('literal', value[1:-1].replace("''", "'"), cast)
    if re.fullmatch(r'-?\d+', value):
        return ('literal', value, cast)
    match = re.fullmatch(r'(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)', value)
    return ('column', match.group(1), cast) if match else None


def _postgres_check_in_values(value):
    values = _split_postgres_top_level(value, ',')
    if not values:
        return None
    parsed = [_postgres_check_operand(item) for item in values]
    if any(item is None or item[0] != 'literal' for item in parsed):
        return None
    return frozenset(parsed)


def _canonical_postgres_check_constraint(
    value, allow_or=False, allow_not_equal=False, allow_greater_equal=False,
):
    """Canonicalize the reflected, literal-only PostgreSQL CHECK grammar.

    This intentionally recognizes only equality, IN/ANY literal checks, and
    NULL tests joined by AND, plus top-level ORs of those controlled AND
    branches.  That is enough for the checked-in direct-upload target
    constraint while refusing functions, expressions, nested boolean forms,
    and unfamiliar casts rather than treating those as cosmetic formatting.
    """
    if not isinstance(value, str):
        return None
    expression = value.strip().casefold()
    branches = _split_postgres_top_level(expression, ' or ')
    if branches is None:
        return None
    if len(branches) > 1:
        if not allow_or:
            return None
        canonical_branches = [
            _canonical_postgres_check_and_branch(
                branch, allow_null_tests=True, allow_not_equal=allow_not_equal,
                allow_greater_equal=allow_greater_equal,
            ) for branch in branches
        ]
        if any(branch is None for branch in canonical_branches) or len(set(canonical_branches)) != len(canonical_branches):
            return None
        return ('or', frozenset(canonical_branches))
    return _canonical_postgres_check_and_branch(
        expression, allow_null_tests=allow_or, allow_not_equal=allow_not_equal,
        allow_greater_equal=allow_greater_equal,
    )


def _canonical_postgres_check_and_branch(
    value, allow_null_tests=False, allow_not_equal=False, allow_greater_equal=False,
):
    atoms = _split_postgres_predicate_and(value)
    if not atoms:
        return None
    parsed = []
    for atom in atoms:
        atom = _strip_sql_parentheses(atom)
        null_match = re.fullmatch(r'(.+?)\s+is\s+(not\s+)?null', atom, re.IGNORECASE)
        if null_match:
            if not allow_null_tests:
                return None
            column = _postgres_check_operand(null_match.group(1))
            if not column or column[0] != 'column':
                return None
            parsed.append((column[1], 'is_not_null' if null_match.group(2) else 'is_null', None, column[2], None))
            continue
        # ``column IN (literal, ...)``.
        in_parts = _split_postgres_top_level(atom, ' in ')
        if in_parts is not None and len(in_parts) == 2:
            column = _postgres_check_operand(in_parts[0])
            values = _strip_sql_parentheses(in_parts[1])
            parsed_values = _postgres_check_in_values(values)
            if not column or column[0] != 'column' or parsed_values is None:
                return None
            parsed.append((column[1], 'in', parsed_values, column[2], None))
            continue
        if allow_not_equal:
            not_equal_parts = _split_postgres_top_level(_strip_sql_parentheses(atom), '<>')
            if not_equal_parts is None or len(not_equal_parts) != 2:
                not_equal_parts = _split_postgres_top_level(_strip_sql_parentheses(atom), '!=')
            if not_equal_parts is not None and len(not_equal_parts) == 2:
                left = _postgres_check_operand(not_equal_parts[0])
                right = _postgres_check_operand(not_equal_parts[1])
                if not left or not right or left[0] != 'column' or right[0] != 'literal':
                    return None
                parsed.append((left[1], '!=', right[1], left[2], right[2]))
                continue
        if allow_greater_equal:
            greater_equal_parts = _split_postgres_top_level(_strip_sql_parentheses(atom), '>=')
            if greater_equal_parts is not None and len(greater_equal_parts) == 2:
                left = _postgres_check_operand(greater_equal_parts[0])
                right = _postgres_check_operand(greater_equal_parts[1])
                if (not left or not right or left[0] != 'column' or right[0] != 'literal'
                        or not re.fullmatch(r'-?\d+', right[1])):
                    return None
                parsed.append((left[1], '>=', right[1], left[2], right[2]))
                continue
        greater_parts = _split_postgres_top_level(_strip_sql_parentheses(atom), '>')
        if greater_parts is not None and len(greater_parts) == 2:
            left = _postgres_check_operand(greater_parts[0])
            right = _postgres_check_operand(greater_parts[1])
            if (not left or not right or left[0] != 'column' or right[0] != 'literal'
                    or not re.fullmatch(r'-?\d+', right[1])):
                return None
            parsed.append((left[1], '>', right[1], left[2], right[2]))
            continue
        equality_parts = _split_postgres_top_level(_strip_sql_parentheses(atom), '=')
        if equality_parts is None or len(equality_parts) != 2:
            return None
        left = _postgres_check_operand(equality_parts[0])
        if not left or left[0] != 'column':
            return None
        column = left[1]
        # ``column = ANY (ARRAY[...])`` is PostgreSQL's reflected IN form.
        right = equality_parts[1]
        any_match = re.fullmatch(r'any\s*\((.*)\)', _strip_sql_parentheses(right), re.IGNORECASE)
        if any_match:
            array, array_cast = _postgres_check_cast_parts(any_match.group(1), allow_inner_casts=True)
            array_match = (re.fullmatch(r'array\s*\[(.*)\]', _strip_sql_parentheses(array), re.IGNORECASE)
                           if array is not None else None)
            values = _postgres_check_in_values(array_match.group(1)) if array_match else None
            if values is None:
                return None
            parsed.append((column, 'in', values, left[2], array_cast))
        else:
            right_operand = _postgres_check_operand(right)
            if not right_operand or right_operand[0] != 'literal':
                return None
            literal = right_operand[1]
            parsed.append((column, '=', literal, left[2], right_operand[2]))
    if len(set(parsed)) != len(parsed):
        return None
    return frozenset(parsed)


def _postgres_check_cast_matches(cast, column_type, array=False):
    """Allow only reflection casts that preserve this metadata column type."""
    if cast is None:
        return True
    if isinstance(cast, tuple):
        # A PostgreSQL 17 coercion chain is acceptable only if *every* step
        # is a representation this audited column type may take.
        return bool(cast) and all(_postgres_check_cast_matches(item, column_type, array=array) for item in cast)
    if array:
        if not cast.endswith('[]'):
            return False
        cast = cast[:-2]
    elif cast.endswith('[]'):
        return False
    rendered = _normalize_type(column_type, postgresql.dialect())
    if rendered.startswith('VARCHAR'):
        length = re.search(r'\((\d+)\)', rendered)
        return cast in {'text', 'varchar', 'charactervarying'} or (
            length is not None and cast in {f'varchar({length.group(1)})', f'charactervarying({length.group(1)})'}
        )
    if rendered == 'TEXT':
        return cast == 'text'
    if rendered == 'BIGINT':
        return cast in {'bigint', 'int8', 'numeric'}
    if rendered == 'INTEGER':
        return cast in {'integer', 'int', 'int4'}
    if rendered == 'SMALLINT':
        return cast in {'smallint', 'int2'}
    return False


def _normalize_postgres_check_for_table(canonical, table):
    if canonical is None:
        return None
    if isinstance(canonical, tuple) and len(canonical) == 2 and canonical[0] == 'or':
        branches = [_normalize_postgres_check_for_table(branch, table) for branch in canonical[1]]
        if any(branch is None for branch in branches) or len(set(branches)) != len(branches):
            return None
        return ('or', frozenset(branches))
    normalized = []
    for atom in canonical:
        column_name, operator, value, column_cast, value_cast = atom
        column = table.c.get(column_name)
        if column is None or not _postgres_check_cast_matches(column_cast, column.type):
            return None
        if operator in {'is_null', 'is_not_null'}:
            if value is not None or value_cast is not None:
                return None
            normalized.append((column_name, operator))
            continue
        if operator == 'in':
            values = []
            for kind, literal, literal_cast in value:
                if kind != 'literal' or not _postgres_check_cast_matches(literal_cast, column.type):
                    return None
                values.append(literal)
            if value_cast is not None and not _postgres_check_cast_matches(value_cast, column.type, array=True):
                return None
            normalized.append((column_name, operator, frozenset(values)))
        else:
            if not _postgres_check_cast_matches(value_cast, column.type):
                return None
            normalized.append((column_name, operator, value))
    return frozenset(normalized)


def _canonical_check_constraint(
    value, dialect, allow_or=False, allow_not_equal=False, allow_greater_equal=False,
):
    if getattr(dialect, 'name', None) == 'postgresql':
        return _canonical_postgres_check_constraint(
            str(value), allow_or=allow_or, allow_not_equal=allow_not_equal,
            allow_greater_equal=allow_greater_equal,
        )
    return _normalize_sql(value)


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
    # PostgreSQL exposes the physical indexes backing PK/UQ constraints too.
    # They are not ORM Index objects.  Only the inspector's explicit marker is
    # trusted; named/unique/partial indexes are never filtered heuristically.
    indexes = [index for index in indexes if not index.get('duplicates_constraint')]
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
                    or actual_nullable != expected.nullable
                    or not _defaults_are_equivalent(
                        _metadata_default(expected), actual.get('default'), expected, table_name, connection.dialect,
                        connection=connection,
                    )):
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
            ('check_constraints', _expected_check_constraints(table, connection.dialect),
             _reflected_check_constraints(inspector, table_name, connection.dialect, table)),
            ('indexes', _expected_indexes(table), _reflected_indexes(inspector, table_name)),
        )
        for label, expected, actual in checks:
            if (label in {'foreign_keys', 'unique_constraints'}
                    and not _constraints_are_equivalent(expected, actual)) or (
                        label not in {'foreign_keys', 'unique_constraints'} and expected != actual):
                differences.append(
                    f'{table_name}.{label} expected={expected!r} actual={actual!r}'
                )
    differences.extend(_partial_index_differences(connection, metadata))
    # SQLAlchemy's generic inspector does not reliably expose trigger bodies.
    # Once a DB has recorded the Phase 3 head, require the database-level
    # append-only guard explicitly rather than trusting the ORM event hook.
    if ('audit_event' in actual_tables and _current_version(connection) == (migration_head_revision(),)):
        differences.extend(_audit_trigger_differences(connection))
    if _current_version(connection) == (migration_head_revision(),):
        differences.extend(_data_api_security_differences(connection))
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
                if (_canonical_postgres_partial_predicate(predicate) is None
                    or _canonical_postgres_partial_predicate(predicate) != actual.get((table, name)))]
    return ['partial index predicates cannot be verified for this database dialect']


def _canonical_postgres_partial_predicate(value):
    """Parse the deliberately tiny predicate language used by revisions 04/06/08.

    PostgreSQL's ``pg_get_expr`` adds parentheses and ``::text`` casts, whose
    formatting changes by server version.  Do not use a loose substring test:
    parse only equality atoms over ``role``/``status`` joined by AND.  Any
    other expression (notably OR, a missing atom, or an unfamiliar cast) is
    rejected by returning ``None``.
    """
    if not isinstance(value, str):
        return None
    expression = _strip_sql_parentheses(value.strip().casefold())
    # The featured-gallery uniqueness contract intentionally uses ``IS TRUE``
    # rather than bare boolean truthiness.  PostgreSQL preserves this shape in
    # pg_get_expr (modulo outer parentheses), so accept exactly that form and
    # reject equivalent-looking but weaker predicates such as ``is_featured``
    # or ``IS NOT FALSE``.
    if re.fullmatch(r'(?:[a-z_][a-z0-9_]*\.)?is_featured\s+is\s+true', expression):
        return ('is_featured', 'is_true')
    nullable_identity = re.fullmatch(
        r'(?:[a-z_][a-z0-9_]*\.)?(created_by_membership_id|voter_user_id)\s+is\s+not\s+null', expression,
    )
    if nullable_identity:
        return (nullable_identity.group(1), 'is_not_null')
    atoms = _split_postgres_predicate_and(expression)
    if not atoms:
        return None
    claim_atoms = set()
    for atom in atoms:
        match = re.fullmatch(
            r'(?:[a-z_][a-z0-9_]*\.)?(invalidated_at|redeemed_at)\s+is\s+null',
            _strip_sql_parentheses(atom),
        )
        if match is None:
            claim_atoms = set()
            break
        claim_atoms.add((match.group(1), 'is_null'))
    if claim_atoms:
        expected_claim_atoms = {('invalidated_at', 'is_null'), ('redeemed_at', 'is_null')}
        return frozenset(claim_atoms) if claim_atoms == expected_claim_atoms and len(atoms) == 2 else None
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


def _data_api_security_differences(connection):
    """Verify PostgreSQL ACL/RLS state that ORM metadata cannot express."""
    if connection.dialect.name != 'postgresql':
        return []
    rows = connection.execute(text("""
        SELECT cls.relname, cls.relrowsecurity, cls.relforcerowsecurity
        FROM pg_class AS cls
        JOIN pg_namespace AS namespace ON namespace.oid = cls.relnamespace
        WHERE namespace.nspname = 'public' AND cls.relkind IN ('r', 'p')
        ORDER BY cls.relname
    """)).all()
    if {row[0] for row in rows} != set(DATA_API_MANAGED_TABLES):
        return ['Data API security table allowlist does not match public tables']
    differences = []
    if any(not enabled or forced for _, enabled, forced in rows):
        differences.append('Data API security requires enabled, non-forced RLS on every managed table')
    policy_count = connection.execute(text("""
        SELECT count(*) FROM pg_policies
        WHERE schemaname = 'public' AND tablename = ANY(:table_names)
    """), {'table_names': list(DATA_API_MANAGED_TABLES)}).scalar_one()
    if policy_count:
        differences.append('Data API security requires no managed-table RLS policies')
    roles = connection.execute(text(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
    )).scalars().all()
    if roles:
        table_grants = connection.execute(text("""
            SELECT count(*)
            FROM pg_class AS cls
            JOIN pg_namespace AS namespace ON namespace.oid = cls.relnamespace
            CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'),
                        ('REFERENCES'), ('TRIGGER'), ('MAINTAIN')) AS privilege(name)
            CROSS JOIN (SELECT rolname FROM pg_roles WHERE rolname = ANY(:roles)) AS role_name
            WHERE namespace.nspname = 'public'
              AND cls.relname = ANY(:table_names)
              AND has_table_privilege(role_name.rolname, cls.oid, privilege.name)
        """), {'roles': list(roles), 'table_names': list(DATA_API_MANAGED_TABLES)}).scalar_one()
        if table_grants:
            differences.append('Data API client role retains effective table privileges')
        sequence_grants = connection.execute(text("""
            SELECT count(*)
            FROM pg_class AS sequence_class
            JOIN pg_depend AS dependency
              ON dependency.classid = 'pg_class'::regclass
             AND dependency.objid = sequence_class.oid
             AND dependency.refclassid = 'pg_class'::regclass
             AND dependency.deptype IN ('a', 'i')
            JOIN pg_class AS table_class ON table_class.oid = dependency.refobjid
            JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
            CROSS JOIN (VALUES ('USAGE'), ('SELECT'), ('UPDATE')) AS privilege(name)
            CROSS JOIN (SELECT rolname FROM pg_roles WHERE rolname = ANY(:roles)) AS role_name
            WHERE sequence_class.relkind = 'S'
              AND table_namespace.nspname = 'public'
              AND table_class.relname = ANY(:table_names)
              AND has_sequence_privilege(role_name.rolname, sequence_class.oid, privilege.name)
        """), {'roles': list(roles), 'table_names': list(DATA_API_MANAGED_TABLES)}).scalar_one()
        if sequence_grants:
            differences.append('Data API client role retains effective sequence privileges')
    public_grants = connection.execute(text("""
        SELECT count(*)
        FROM pg_class AS cls
        JOIN pg_namespace AS namespace ON namespace.oid = cls.relnamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(cls.relacl, acldefault('r', cls.relowner))) AS acl
        WHERE namespace.nspname = 'public'
          AND cls.relname = ANY(:table_names) AND acl.grantee = 0
    """), {'table_names': list(DATA_API_MANAGED_TABLES)}).scalar_one()
    if public_grants:
        differences.append('PUBLIC retains managed-table privileges')
    default_grants = connection.execute(text("""
        SELECT count(*)
        FROM pg_default_acl AS defaults
        CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
        WHERE defaults.defaclrole = current_user::regrole
          AND defaults.defaclnamespace = 'public'::regnamespace
          AND defaults.defaclobjtype IN ('r', 'S')
          AND (acl.grantee = 0 OR acl.grantee IN (
              SELECT oid FROM pg_roles WHERE rolname IN ('anon', 'authenticated')
          ))
    """)).scalar_one()
    if default_grants:
        differences.append('Migration owner has unsafe public-schema default privileges')
    global_default_grants = connection.execute(text("""
        SELECT count(*)
        FROM pg_default_acl AS defaults
        CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
        WHERE defaults.defaclrole = current_user::regrole
          AND defaults.defaclnamespace = 0
          AND defaults.defaclobjtype IN ('r', 'S')
          AND (acl.grantee = 0 OR acl.grantee IN (
              SELECT oid FROM pg_roles WHERE rolname IN ('anon', 'authenticated')
          ))
    """)).scalar_one()
    if global_default_grants:
        differences.append('Migration owner has unsafe global default privileges')
    return differences


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
            SELECT trigger.tgname, trigger.tgtype, trigger.tgenabled, trigger.tgqual, trigger.tgattr, trigger.tgfoid,
                   function.oid, function_namespace.nspname, function.proname,
                   pg_get_function_identity_arguments(function.oid), return_type.typname,
                   language.lanname, function.prosrc
            FROM pg_trigger AS trigger
            JOIN pg_proc AS function ON function.oid = trigger.tgfoid
            JOIN pg_namespace AS function_namespace ON function_namespace.oid = function.pronamespace
            JOIN pg_type AS return_type ON return_type.oid = function.prorettype
            JOIN pg_language AS language ON language.oid = function.prolang
            WHERE trigger.tgrelid = (
                SELECT table_class.oid
                FROM pg_class AS table_class
                JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_class.relnamespace
                WHERE table_namespace.nspname = 'public'
                  AND table_class.relname = 'audit_event'
                  AND table_class.relkind IN ('r', 'p')
            )
              AND NOT trigger.tgisinternal
        """)).all()
        expected = {
            'audit_event_no_update': 19,  # BEFORE | ROW | UPDATE
            'audit_event_no_delete': 11,  # BEFORE | ROW | DELETE
        }
        actual = {}
        invalid = False
        for row in rows:
            (name, trigger_type, enabled, trigger_qualifier, trigger_attributes, trigger_function_oid, function_oid, function_schema,
             function_name, identity_arguments, return_type, language, body) = row
            if name in actual:
                invalid = True
            actual[name] = row
            if (name not in expected or int(trigger_type) != expected[name] or enabled != 'O'
                    or trigger_qualifier is not None or not _postgres_trigger_attributes_are_empty(trigger_attributes)
                    or trigger_function_oid != function_oid or function_schema != 'public'
                    or function_name != 'audit_event_append_only' or identity_arguments != ''
                    or return_type != 'trigger' or language != 'plpgsql'
                    or not _is_exact_postgres_audit_function_body(body)):
                invalid = True
        defects = [f'audit_event trigger {name} is missing or invalid'
                   for name in expected if name not in actual]
        if invalid or set(actual) != set(expected):
            defects.append('audit_event trigger/function contract is invalid')
        return defects
    # An unknown dialect cannot prove the invariant.
    return ['audit_event triggers cannot be verified for this database dialect']


def _is_exact_postgres_audit_function_body(body):
    """Accept only the unconditional append-only function installed by v04.

    ``pg_proc.prosrc`` provides the body without DDL decoration. Whitespace is
    harmless; every executable token is part of the security contract.
    """
    if not isinstance(body, str):
        return False
    return re.sub(r'\s+', '', body).casefold() == "beginraiseexception'audit_eventisappend-only';end;"


def _postgres_trigger_attributes_are_empty(attributes):
    """Accept only PostgreSQL's empty int2vector representation for tgattr."""
    if isinstance(attributes, str):
        return attributes.strip() == ''
    if isinstance(attributes, (tuple, list)):
        return len(attributes) == 0
    return False


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
    if revision == PHASE4_REVISION:
        return phase4_metadata()
    if revision == PHASE6_REVISION:
        return phase6_metadata()
    if revision == PHASE7_REVISION:
        return phase7_metadata()
    if revision == PHASE8_REVISION:
        return phase8_metadata()
    if revision == PHASE9_REVISION:
        return phase9_metadata()
    if revision == PHASE10_REVISION:
        return phase10_metadata()
    if revision == PHASE11_REVISION:
        return phase11_metadata()
    if revision == PHASE12_REVISION:
        return phase12_metadata()
    if revision == PHASE13_REVISION:
        # Revision 13 hardens PostgreSQL access only; its structural snapshot
        # is identical to revision 12. Keep this historical snapshot explicit
        # so upgrades from the recorded 13 head can be safely preflighted.
        return phase12_metadata()
    if revision == PHASE14_REVISION:
        return phase14_metadata()
    if revision == PHASE15_REVISION:
        return phase15_metadata()
    if revision == PHASE16_REVISION:
        return phase16_metadata()
    if revision == PHASE17_REVISION:
        return phase17_metadata()
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
