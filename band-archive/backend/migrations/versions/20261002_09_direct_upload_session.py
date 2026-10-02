"""Add durable tenant-scoped sessions for v2 direct uploads.

Revision ID: 20261002_09
Revises: 20261002_08
"""

from alembic import context, op
import sqlalchemy as sa


revision = '20261002_09'
down_revision = '20261002_08'
branch_labels = None
depends_on = None


def _assert_downgrade_safe():
    if context.is_offline_mode():
        return
    count = op.get_bind().execute(sa.text('SELECT count(*) FROM direct_upload_session')).scalar_one()
    if count:
        raise RuntimeError('Direct-upload sessions exist; downgrade would discard upload capabilities and state.')


def upgrade():
    op.create_table(
        'direct_upload_session',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('band_id', sa.Integer(), nullable=False),
        sa.Column('created_by_membership_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('object_key', sa.String(length=500), nullable=False),
        sa.Column('original_filename', sa.String(length=200), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('declared_bytes', sa.BigInteger(), nullable=False),
        sa.Column('song_id', sa.Integer(), nullable=True),
        sa.Column('rehearsal_id', sa.Integer(), nullable=True),
        sa.Column('member_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='initiated'),
        sa.Column('capability_token_hash', sa.String(length=255), nullable=False),
        sa.Column('media_id', sa.Integer(), nullable=True),
        sa.Column('personal_log_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('completion_started_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint("kind IN ('media', 'personal_log')", name='ck_direct_upload_session_kind'),
        sa.CheckConstraint('declared_bytes > 0', name='ck_direct_upload_session_bytes'),
        sa.CheckConstraint("status IN ('initiated', 'completing', 'completed', 'expired', 'failed')",
                           name='ck_direct_upload_session_status'),
        sa.CheckConstraint(
            "(kind = 'media' AND song_id IS NOT NULL AND member_id IS NULL) OR "
            "(kind = 'personal_log' AND song_id IS NULL AND rehearsal_id IS NULL AND member_id IS NOT NULL)",
            name='ck_direct_upload_session_target',
        ),
        sa.ForeignKeyConstraint(['band_id'], ['band.id']),
        sa.ForeignKeyConstraint(['created_by_membership_id'], ['band_membership.id']),
        sa.ForeignKeyConstraint(['song_id'], ['song.id']),
        sa.ForeignKeyConstraint(['rehearsal_id'], ['rehearsal.id']),
        sa.ForeignKeyConstraint(['member_id'], ['member.id']),
        sa.ForeignKeyConstraint(['media_id'], ['media.id']),
        sa.ForeignKeyConstraint(['personal_log_id'], ['personal_log.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_direct_upload_session_band_id', 'direct_upload_session', ['band_id'])
    op.create_index('ix_direct_upload_session_created_by_membership_id', 'direct_upload_session',
                    ['created_by_membership_id'])


def downgrade():
    _assert_downgrade_safe()
    op.drop_index('ix_direct_upload_session_created_by_membership_id', table_name='direct_upload_session')
    op.drop_index('ix_direct_upload_session_band_id', table_name='direct_upload_session')
    op.drop_table('direct_upload_session')
