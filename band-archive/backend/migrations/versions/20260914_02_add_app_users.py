"""Add local Supabase user profiles and one-time admin bootstrap state.

Revision ID: 20260914_02
Revises: 20260914_01
Create Date: 2026-09-14
"""

from alembic import op
import sqlalchemy as sa


revision = '20260914_02'
down_revision = '20260914_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'app_user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('supabase_user_id', sa.String(length=64), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('is_platform_admin', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('supabase_user_id'),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_app_user_supabase_user_id', 'app_user', ['supabase_user_id'], unique=True)
    op.create_index('ix_app_user_email', 'app_user', ['email'], unique=True)
    op.create_table(
        'auth_bootstrap_state',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('claimed_by_user_id', sa.Integer(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['claimed_by_user_id'], ['app_user.id']),
        sa.PrimaryKeyConstraint('key'),
        sa.UniqueConstraint('claimed_by_user_id'),
    )


def downgrade():
    op.drop_table('auth_bootstrap_state')
    op.drop_index('ix_app_user_email', table_name='app_user')
    op.drop_index('ix_app_user_supabase_user_id', table_name='app_user')
    op.drop_table('app_user')
