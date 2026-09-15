"""Add isolated Phase 3 band membership and audit domain.

Revision ID: 20260915_03
Revises: 20260914_02
Create Date: 2026-09-15
"""

from alembic import op
import sqlalchemy as sa


revision = '20260915_03'
down_revision = '20260914_02'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'band',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('slug', sa.String(length=80), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('default_visibility', sa.String(length=20), nullable=False, server_default='PUBLIC'),
        sa.Column('member_roster_public', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('storage_quota_bytes', sa.BigInteger(), nullable=False, server_default=str(20 * 1024 * 1024 * 1024)),
        sa.Column('created_by_user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('purge_after', sa.DateTime(), nullable=True),
        sa.CheckConstraint("default_visibility IN ('PUBLIC', 'MEMBERS')", name='ck_band_visibility'),
        sa.CheckConstraint('storage_quota_bytes > 0', name='ck_band_storage_quota'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['app_user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_table(
        'band_membership',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('band_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default='member'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('left_at', sa.DateTime(), nullable=True),
        sa.Column('acted_by_user_id', sa.Integer(), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')", name='ck_band_membership_role'),
        sa.CheckConstraint("status IN ('pending', 'active', 'rejected', 'left')", name='ck_band_membership_status'),
        sa.ForeignKeyConstraint(['acted_by_user_id'], ['app_user.id']),
        sa.ForeignKeyConstraint(['band_id'], ['band.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('band_id', 'user_id', name='uq_band_membership_user'),
    )
    op.create_index('ix_band_membership_band_id', 'band_membership', ['band_id'])
    op.create_index('ix_band_membership_user_id', 'band_membership', ['user_id'])
    op.create_index(
        'uq_band_membership_active_owner', 'band_membership', ['band_id'], unique=True,
        sqlite_where=sa.text("role = 'owner' AND status = 'active'"),
        postgresql_where=sa.text("role = 'owner' AND status = 'active'"),
    )
    op.create_table(
        'band_creation_request',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('requested_by_user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('slug', sa.String(length=80), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('reviewed_by_user_id', sa.Integer(), nullable=True),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.Column('band_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'active', 'rejected')", name='ck_band_creation_request_status'),
        sa.ForeignKeyConstraint(['band_id'], ['band.id']),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['app_user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by_user_id'], ['app_user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('band_id'),
    )
    op.create_index('ix_band_creation_request_requested_by_user_id', 'band_creation_request', ['requested_by_user_id'])
    op.create_index(
        'uq_band_creation_request_pending_user_slug', 'band_creation_request',
        ['requested_by_user_id', 'slug'], unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        'band_invite_code',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('band_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['band_id'], ['band.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['app_user.id']),
        sa.ForeignKeyConstraint(['revoked_by_user_id'], ['app_user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_band_invite_code_band_id', 'band_invite_code', ['band_id'])
    op.create_table(
        'audit_event',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('band_id', sa.Integer(), nullable=True),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('target_type', sa.String(length=100), nullable=True),
        sa.Column('target_id', sa.String(length=100), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['actor_user_id'], ['app_user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['band_id'], ['band.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_event_actor_user_id', 'audit_event', ['actor_user_id'])
    op.create_index('ix_audit_event_band_id', 'audit_event', ['band_id'])
    _create_audit_immutability_triggers()


def _create_audit_immutability_triggers():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        op.execute("""
            CREATE TRIGGER audit_event_no_update
            BEFORE UPDATE ON audit_event
            BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER audit_event_no_delete
            BEFORE DELETE ON audit_event
            BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END
        """)
    elif bind.dialect.name == 'postgresql':
        op.execute("""
            CREATE FUNCTION audit_event_append_only() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'audit_event is append-only'; END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("CREATE TRIGGER audit_event_no_update BEFORE UPDATE ON audit_event "
                   "FOR EACH ROW EXECUTE FUNCTION audit_event_append_only()")
        op.execute("CREATE TRIGGER audit_event_no_delete BEFORE DELETE ON audit_event "
                   "FOR EACH ROW EXECUTE FUNCTION audit_event_append_only()")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        op.execute('DROP TRIGGER IF EXISTS audit_event_no_delete')
        op.execute('DROP TRIGGER IF EXISTS audit_event_no_update')
    elif bind.dialect.name == 'postgresql':
        op.execute('DROP TRIGGER IF EXISTS audit_event_no_delete ON audit_event')
        op.execute('DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event')
        op.execute('DROP FUNCTION IF EXISTS audit_event_append_only()')
    op.drop_index('ix_audit_event_band_id', table_name='audit_event')
    op.drop_index('ix_audit_event_actor_user_id', table_name='audit_event')
    op.drop_table('audit_event')
    op.drop_index('ix_band_invite_code_band_id', table_name='band_invite_code')
    op.drop_table('band_invite_code')
    op.drop_index('ix_band_creation_request_requested_by_user_id', table_name='band_creation_request')
    op.drop_index('uq_band_creation_request_pending_user_slug', table_name='band_creation_request')
    op.drop_table('band_creation_request')
    op.drop_index('ix_band_membership_user_id', table_name='band_membership')
    op.drop_index('ix_band_membership_band_id', table_name='band_membership')
    op.drop_index('uq_band_membership_active_owner', table_name='band_membership')
    op.drop_table('band_membership')
    op.drop_table('band')
