"""Phase 4 expand-only tenant ownership and canonical object-key columns.

This revision deliberately does not create the legacy band, rewrite rows, or
make any column required.  Those are an explicit offline backfill followed by
a separately guarded contract step; therefore upgrading an old production
database to this head is safe and reversible at the application level.
"""

from alembic import op
import sqlalchemy as sa


revision = '20260916_05'
down_revision = '20260916_04'
branch_labels = None
depends_on = None


def _expand(table_name, columns, foreign_keys=(), checks=()):
    """Batch rebuild keeps SQLite FK additions real and PostgreSQL portable."""
    with op.batch_alter_table(table_name, recreate='always') as batch:
        for column in columns:
            batch.add_column(column)
        for name, local, remote in foreign_keys:
            batch.create_foreign_key(name, remote.split('.')[0], [local], [remote.split('.')[1]])
        for name, sqltext in checks:
            batch.create_check_constraint(name, sqltext)
    for column in columns:
        if column.name in {'band_id', 'created_by_membership_id', 'linked_user_id'}:
            op.create_index(f'ix_{table_name}_{column.name}', table_name, [column.name], unique=False)


def _scope_columns(include_visibility=False, legacy=False):
    values = [
        sa.Column('band_id', sa.Integer(), nullable=True),
        sa.Column('created_by_membership_id', sa.Integer(), nullable=True),
    ]
    if include_visibility:
        values.append(sa.Column('visibility', sa.String(length=20), nullable=True, server_default='PUBLIC'))
    if legacy:
        values.append(sa.Column('is_legacy', sa.Boolean(), nullable=False, server_default='0'))
    return values


def upgrade():
    # A NULL creator is reserved for the imported, ownerless legacy band;
    # ordinary band creation still validates a requester in its service path.
    with op.batch_alter_table('band', recreate='always') as batch:
        batch.alter_column('created_by_user_id', existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column('is_legacy_import', sa.Boolean(), nullable=False, server_default='0'))
    op.create_table(
        'tenant_backfill_state',
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('report_json', sa.Text(), nullable=True),
        sa.Column('validated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('planned', 'applying', 'validated', 'failed')",
                           name='ck_tenant_backfill_state_status'),
        sa.PrimaryKeyConstraint('key'),
    )

    _expand('song', _scope_columns(True) + [sa.Column('sheet_music_object_key', sa.String(length=500), nullable=True)],
            [('fk_song_band', 'band_id', 'band.id'),
             ('fk_song_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
            [('ck_song_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")])
    _expand('rehearsal', _scope_columns(True),
            [('fk_rehearsal_band', 'band_id', 'band.id'),
             ('fk_rehearsal_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
            [('ck_rehearsal_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")])
    _expand('media', _scope_columns(True) + [
        sa.Column('object_key', sa.String(length=500), nullable=True),
        sa.Column('audio_object_key', sa.String(length=500), nullable=True),
        sa.Column('video_720_object_key', sa.String(length=500), nullable=True),
    ], [('fk_media_band', 'band_id', 'band.id'),
        ('fk_media_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
        [('ck_media_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")])
    _expand('member', [sa.Column('band_id', sa.Integer(), nullable=True),
                       sa.Column('linked_user_id', sa.Integer(), nullable=True)],
            [('fk_member_band', 'band_id', 'band.id'), ('fk_member_linked_user', 'linked_user_id', 'app_user.id')])
    _expand('personal_log', _scope_columns(True) + [
        sa.Column('object_key', sa.String(length=500), nullable=True),
        sa.Column('audio_object_key', sa.String(length=500), nullable=True),
    ], [('fk_personal_log_band', 'band_id', 'band.id'),
        ('fk_personal_log_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
        [('ck_personal_log_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")])
    _expand('gallery_image', _scope_columns(True) + [sa.Column('object_key', sa.String(length=500), nullable=True)],
            [('fk_gallery_image_band', 'band_id', 'band.id'),
             ('fk_gallery_image_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
            [('ck_gallery_image_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")])
    _expand('announcement', _scope_columns(True),
            [('fk_announcement_band', 'band_id', 'band.id'),
             ('fk_announcement_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
            [('ck_announcement_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")])
    _expand('song_suggestion', _scope_columns(True),
            [('fk_song_suggestion_band', 'band_id', 'band.id'),
             ('fk_song_suggestion_created_by_membership', 'created_by_membership_id', 'band_membership.id')],
            [('ck_song_suggestion_visibility', "visibility IN ('PUBLIC', 'MEMBERS')")])
    _expand('comment', _scope_columns(False, True),
            [('fk_comment_band', 'band_id', 'band.id'),
             ('fk_comment_created_by_membership', 'created_by_membership_id', 'band_membership.id')])
    _expand('song_vote', _scope_columns(False, True),
            [('fk_song_vote_band', 'band_id', 'band.id'),
             ('fk_song_vote_created_by_membership', 'created_by_membership_id', 'band_membership.id')])
    _expand('media_vote', _scope_columns(False, True),
            [('fk_media_vote_band', 'band_id', 'band.id'),
             ('fk_media_vote_created_by_membership', 'created_by_membership_id', 'band_membership.id')])
    _expand('multipart_upload_session', _scope_columns(False, True),
            [('fk_multipart_upload_session_band', 'band_id', 'band.id'),
             ('fk_multipart_upload_session_created_by_membership', 'created_by_membership_id', 'band_membership.id')])
    _expand('personal_log_multipart_upload_session', _scope_columns(False, True),
            [('fk_personal_log_multipart_upload_session_band', 'band_id', 'band.id'),
             ('fk_personal_log_multipart_upload_session_created_by_membership', 'created_by_membership_id', 'band_membership.id')])


def downgrade():
    # This is an expand-only operational release.  A destructive downgrade
    # could discard tenant assignments made by a backfill, so operators must
    # restore a pre-expand backup instead.
    raise RuntimeError('Phase 4 expand revision is intentionally not downgradeable')
