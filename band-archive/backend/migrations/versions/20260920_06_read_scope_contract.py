"""Contract validated tenant scopes for Phase 6 read APIs.

Revision ID: 20260920_06
Revises: 20260916_05
"""

from alembic import context, op
import sqlalchemy as sa

from tenant_backfill import TenantBackfillError, validate_relationships


revision = '20260920_06'
down_revision = '20260916_05'
branch_labels = None
depends_on = None


_BAND_TABLES = (
    'song', 'rehearsal', 'media', 'member', 'personal_log', 'gallery_image',
    'announcement', 'song_suggestion', 'comment', 'song_vote', 'media_vote',
    'multipart_upload_session', 'personal_log_multipart_upload_session',
)
_VISIBILITY_TABLES = (
    'song', 'rehearsal', 'media', 'personal_log', 'gallery_image',
    'announcement', 'song_suggestion',
)
_READ_INDEXES = (
    ('ix_song_band_visibility_id', 'song', ('band_id', 'visibility', 'id')),
    ('ix_rehearsal_band_visibility_date_id', 'rehearsal', ('band_id', 'visibility', 'date', 'id')),
    ('ix_media_band_song_visibility_score_id', 'media', ('band_id', 'song_id', 'visibility', 'vote_score', 'id')),
    ('ix_media_band_rehearsal_visibility_score_id', 'media', ('band_id', 'rehearsal_id', 'visibility', 'vote_score', 'id')),
    ('ix_personal_log_band_member_visibility_created_id', 'personal_log',
     ('band_id', 'member_id', 'visibility', 'created_at', 'id')),
    ('ix_gallery_image_band_visibility_created_id', 'gallery_image',
     ('band_id', 'visibility', 'created_at', 'id')),
    ('ix_comment_band_media_parent_created_id', 'comment',
     ('band_id', 'media_id', 'parent_id', 'created_at', 'id')),
    ('ix_comment_band_personal_log_parent_created_id', 'comment',
     ('band_id', 'personal_log_id', 'parent_id', 'created_at', 'id')),
)


def _is_sqlite():
    return op.get_bind().dialect.name == 'sqlite'


def _contract_defects(connection):
    """Return defects which make a tenant-filtered read unsafe to enable."""
    defects = []
    status = connection.execute(sa.text(
        "SELECT status FROM tenant_backfill_state WHERE key = 'phase4_tenant_scope'"
    )).scalar_one_or_none()
    if status != 'validated':
        defects.append('tenant_backfill_state.phase4_tenant_scope is not validated')

    for table in _BAND_TABLES:
        row_id = connection.execute(sa.text(
            f'SELECT id FROM {table} WHERE band_id IS NULL OR '
            'band_id NOT IN (SELECT id FROM band) LIMIT 1'
        )).scalar_one_or_none()
        if row_id is not None:
            defects.append(f'{table}.band_id is NULL or invalid (id={row_id})')
    for table in _VISIBILITY_TABLES:
        row_id = connection.execute(sa.text(
            f"SELECT id FROM {table} WHERE visibility IS NULL "
            "OR visibility NOT IN ('PUBLIC', 'MEMBERS') LIMIT 1"
        )).scalar_one_or_none()
        if row_id is not None:
            defects.append(f'{table}.visibility is NULL or invalid (id={row_id})')

    # Phase 6 deliberately reuses the Phase 4 relationship contract.  This
    # keeps every denormalized tenant scope (including creator memberships and
    # recursive comment scope) governed by one fail-closed definition.
    try:
        relationship_defects, _ = validate_relationships(connection, -1)
    except TenantBackfillError as error:
        defects.append(f'comment scope is invalid ({error})')
    else:
        for defect in relationship_defects:
            defects.append(
                f"{defect['relationship']} crosses tenant scope (ids={defect['ids']})"
            )

    duplicate = connection.execute(sa.text(
        'SELECT band_id FROM announcement GROUP BY band_id HAVING count(*) > 1 LIMIT 1'
    )).scalar_one_or_none()
    if duplicate is not None:
        defects.append(f'announcement has multiple rows for band_id={duplicate}')
    duplicate = connection.execute(sa.text(
        'SELECT band_id FROM gallery_image WHERE is_featured IS TRUE '
        'GROUP BY band_id HAVING count(*) > 1 LIMIT 1'
    )).scalar_one_or_none()
    if duplicate is not None:
        defects.append(f'gallery_image has multiple featured rows for band_id={duplicate}')
    return defects


def _assert_contract():
    # Offline SQL generation has no database state to inspect. Live upgrades
    # must prove all preconditions before the first DDL operation.
    if context.is_offline_mode():
        return
    defects = _contract_defects(op.get_bind())
    if defects:
        raise RuntimeError('Phase 6 read-scope contract is not satisfied: ' + '; '.join(defects))


def _tighten_table(table, has_visibility):
    if _is_sqlite():
        with op.batch_alter_table(table, recreate='always') as batch:
            batch.alter_column('band_id', existing_type=sa.Integer(), nullable=False)
            if has_visibility:
                batch.alter_column('visibility', existing_type=sa.String(length=20), nullable=False)
            if table == 'announcement':
                batch.create_unique_constraint('uq_announcement_band', ['band_id'])
    else:
        op.alter_column(table, 'band_id', existing_type=sa.Integer(), nullable=False)
        if has_visibility:
            op.alter_column(table, 'visibility', existing_type=sa.String(length=20), nullable=False)
        if table == 'announcement':
            op.create_unique_constraint('uq_announcement_band', table, ['band_id'])


def _loosen_table(table, has_visibility):
    if _is_sqlite():
        with op.batch_alter_table(table, recreate='always') as batch:
            if table == 'announcement':
                batch.drop_constraint('uq_announcement_band', type_='unique')
            if has_visibility:
                batch.alter_column('visibility', existing_type=sa.String(length=20), nullable=True)
            batch.alter_column('band_id', existing_type=sa.Integer(), nullable=True)
    else:
        if table == 'announcement':
            op.drop_constraint('uq_announcement_band', table, type_='unique')
        if has_visibility:
            op.alter_column(table, 'visibility', existing_type=sa.String(length=20), nullable=True)
        op.alter_column(table, 'band_id', existing_type=sa.Integer(), nullable=True)


def upgrade():
    _assert_contract()
    for table in _BAND_TABLES:
        _tighten_table(table, table in _VISIBILITY_TABLES)
    op.create_index(
        'uq_gallery_image_featured_band', 'gallery_image', ['band_id'], unique=True,
        sqlite_where=sa.text('is_featured IS TRUE'),
        postgresql_where=sa.text('is_featured IS TRUE'),
    )
    for name, table, columns in _READ_INDEXES:
        op.create_index(name, table, list(columns), unique=False)


def downgrade():
    for name, table, _columns in reversed(_READ_INDEXES):
        op.drop_index(name, table_name=table)
    op.drop_index('uq_gallery_image_featured_band', table_name='gallery_image')
    for table in reversed(_BAND_TABLES):
        _loosen_table(table, table in _VISIBILITY_TABLES)
