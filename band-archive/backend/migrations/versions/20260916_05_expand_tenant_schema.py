"""Phase 4 tenant schema expansion.

This pre-release revision uses SQLite batch rebuilds only where SQLite needs
them. PostgreSQL uses native ALTER operations so offline ``--sql`` generation
never asks Alembic to reflect a live table. Its inverse is pre-backfill only.
"""

from alembic import context, op
import sqlalchemy as sa


revision = '20260916_05'
down_revision = '20260916_04'
branch_labels = None
depends_on = None


def _is_sqlite():
    return op.get_bind().dialect.name == 'sqlite'


def _index_name(table_name, column_name):
    # PostgreSQL identifiers are limited to 63 bytes.  Keep the one verbose
    # legacy table's explicit name aligned with its ORM metadata.
    if (table_name, column_name) == ('personal_log_multipart_upload_session', 'created_by_membership_id'):
        return 'ix_pl_multipart_session_creator'
    return f'ix_{table_name}_{column_name}'


def _expand(table_name, columns, foreign_keys=(), checks=()):
    """Expand without PostgreSQL batch/reflection operations."""
    if _is_sqlite():
        with op.batch_alter_table(table_name, recreate='always') as batch:
            for column in columns:
                batch.add_column(column)
            for name, local, remote in foreign_keys:
                batch.create_foreign_key(name, remote.split('.')[0], [local], [remote.split('.')[1]])
            for name, sqltext in checks:
                batch.create_check_constraint(name, sqltext)
    else:
        for column in columns:
            op.add_column(table_name, column)
        for name, local, remote in foreign_keys:
            op.create_foreign_key(name, table_name, remote.split('.')[0], [local], [remote.split('.')[1]])
        for name, sqltext in checks:
            op.create_check_constraint(name, table_name, sqltext)
    for column in columns:
        if column.name in {'band_id', 'created_by_membership_id', 'linked_user_id'}:
            op.create_index(_index_name(table_name, column.name), table_name, [column.name], unique=False)


def _shrink(table_name, columns, foreign_keys=(), checks=()):
    """Reverse ``_expand`` without requiring PostgreSQL table reflection."""
    for column in columns:
        if column.name in {'band_id', 'created_by_membership_id', 'linked_user_id'}:
            op.drop_index(_index_name(table_name, column.name), table_name=table_name)
    if _is_sqlite():
        with op.batch_alter_table(table_name, recreate='always') as batch:
            for name, _sqltext in checks:
                batch.drop_constraint(name, type_='check')
            for name, _local, _remote in foreign_keys:
                batch.drop_constraint(name, type_='foreignkey')
            for column in reversed(columns):
                batch.drop_column(column.name)
    else:
        for name, _sqltext in checks:
            op.drop_constraint(name, table_name, type_='check')
        for name, _local, _remote in foreign_keys:
            op.drop_constraint(name, table_name, type_='foreignkey')
        for column in reversed(columns):
            op.drop_column(table_name, column.name)


def _scope_columns(include_visibility=False, legacy=False):
    columns = [
        sa.Column('band_id', sa.Integer(), nullable=True),
        sa.Column('created_by_membership_id', sa.Integer(), nullable=True),
    ]
    if include_visibility:
        columns.append(sa.Column('visibility', sa.String(length=20), nullable=True, server_default='PUBLIC'))
    if legacy:
        columns.append(sa.Column('is_legacy', sa.Boolean(), nullable=False, server_default='0'))
    return columns


_EXPANSIONS = (
    ('song', _scope_columns(True) + [sa.Column('sheet_music_object_key', sa.String(length=500), nullable=True)],
     [('fk_song_band', 'band_id', 'band.id'), ('fk_song_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
     [('ck_song_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")]),
    ('rehearsal', _scope_columns(True),
     [('fk_rehearsal_band', 'band_id', 'band.id'), ('fk_rehearsal_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
     [('ck_rehearsal_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")]),
    ('media', _scope_columns(True) + [
        sa.Column('object_key', sa.String(length=500), nullable=True),
        sa.Column('audio_object_key', sa.String(length=500), nullable=True),
        sa.Column('video_720_object_key', sa.String(length=500), nullable=True),
    ], [('fk_media_band', 'band_id', 'band.id'), ('fk_media_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
        [('ck_media_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")]),
    ('member', [sa.Column('band_id', sa.Integer(), nullable=True), sa.Column('linked_user_id', sa.Integer(), nullable=True)],
     [('fk_member_band', 'band_id', 'band.id'), ('fk_member_linked_user', 'linked_user_id', 'app_user.id')], []),
    ('personal_log', _scope_columns(True) + [
        sa.Column('object_key', sa.String(length=500), nullable=True),
        sa.Column('audio_object_key', sa.String(length=500), nullable=True),
    ], [('fk_personal_log_band', 'band_id', 'band.id'), ('fk_personal_log_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
        [('ck_personal_log_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")]),
    ('gallery_image', _scope_columns(True) + [sa.Column('object_key', sa.String(length=500), nullable=True)],
     [('fk_gallery_image_band', 'band_id', 'band.id'), ('fk_gallery_image_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
     [('ck_gallery_image_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")]),
    ('announcement', _scope_columns(True),
     [('fk_announcement_band', 'band_id', 'band.id'), ('fk_announcement_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
     [('ck_announcement_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")]),
    ('song_suggestion', _scope_columns(True),
     [('fk_song_suggestion_band', 'band_id', 'band.id'), ('fk_song_suggestion_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
     [('ck_song_suggestion_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")]),
    ('comment', _scope_columns(False, True),
     [('fk_comment_band', 'band_id', 'band.id'), ('fk_comment_created_by_membership', 'created_by_membership_id', 'band_membership.id')], []),
    ('song_vote', _scope_columns(False, True),
     [('fk_song_vote_band', 'band_id', 'band.id'), ('fk_song_vote_created_by_membership', 'created_by_membership_id', 'band_membership.id')], []),
    ('media_vote', _scope_columns(False, True),
     [('fk_media_vote_band', 'band_id', 'band.id'), ('fk_media_vote_created_by_membership', 'created_by_membership_id', 'band_membership.id')], []),
    ('multipart_upload_session', _scope_columns(False, True),
     [('fk_multipart_upload_session_band', 'band_id', 'band.id'), ('fk_multipart_upload_session_created_by_membership', 'created_by_membership_id', 'band_membership.id')], []),
    ('personal_log_multipart_upload_session', _scope_columns(False, True),
     [('fk_personal_log_multipart_upload_session_band', 'band_id', 'band.id'), ('fk_personal_log_multipart_upload_session_created_by_membership', 'created_by_membership_id', 'band_membership.id')], []),
)


def upgrade():
    if _is_sqlite():
        with op.batch_alter_table('band', recreate='always') as batch:
            batch.alter_column('created_by_user_id', existing_type=sa.Integer(), nullable=True)
            batch.add_column(sa.Column('is_legacy_import', sa.Boolean(), nullable=False, server_default='0'))
    else:
        op.alter_column('band', 'created_by_user_id', existing_type=sa.Integer(), nullable=True)
        op.add_column('band', sa.Column('is_legacy_import', sa.Boolean(), nullable=False, server_default='0'))
    op.create_table(
        'tenant_backfill_state',
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('report_json', sa.Text(), nullable=True),
        sa.Column('validated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('planned', 'applying', 'validated', 'failed')", name='ck_tenant_backfill_state_status'),
        sa.PrimaryKeyConstraint('key'),
    )
    for table_name, columns, foreign_keys, checks in _EXPANSIONS:
        _expand(table_name, columns, foreign_keys, checks)


def _assert_backfill_not_started():
    if context.is_offline_mode():
        return
    state = op.get_bind().execute(sa.text(
        "SELECT status FROM tenant_backfill_state WHERE key = 'phase4_tenant_scope'"
    )).scalar_one_or_none()
    if state is not None:
        raise RuntimeError('Phase 4 cannot be downgraded after tenant backfill has started.')


def downgrade():
    """Pre-backfill-only inverse; PostgreSQL remains offline-compilable."""
    _assert_backfill_not_started()
    for table_name, columns, foreign_keys, checks in reversed(_EXPANSIONS):
        _shrink(table_name, columns, foreign_keys, checks)
    op.drop_table('tenant_backfill_state')
    if _is_sqlite():
        with op.batch_alter_table('band', recreate='always') as batch:
            batch.drop_column('is_legacy_import')
            batch.alter_column('created_by_user_id', existing_type=sa.Integer(), nullable=False)
    else:
        op.drop_column('band', 'is_legacy_import')
        op.alter_column('band', 'created_by_user_id', existing_type=sa.Integer(), nullable=False)
