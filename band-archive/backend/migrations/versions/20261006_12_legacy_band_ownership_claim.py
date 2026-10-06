"""Add one-time ownership claims for ownerless legacy bands.

Revision ID: 20261006_12
Revises: 20261004_11
"""

from alembic import op
import sqlalchemy as sa


revision = '20261006_12'
down_revision = '20261004_11'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'legacy_band_claim_token',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('band_id', sa.Integer(), nullable=False),
        sa.Column('purpose', sa.String(length=40), nullable=False, server_default='initial_owner'),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('issued_by_user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('invalidated_at', sa.DateTime(), nullable=True),
        sa.Column('invalidated_by_user_id', sa.Integer(), nullable=True),
        sa.Column('redeemed_at', sa.DateTime(), nullable=True),
        sa.Column('redeemed_by_user_id', sa.Integer(), nullable=True),
        sa.CheckConstraint("purpose = 'initial_owner'", name='ck_legacy_band_claim_purpose'),
        sa.ForeignKeyConstraint(['band_id'], ['band.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['issued_by_user_id'], ['app_user.id']),
        sa.ForeignKeyConstraint(['invalidated_by_user_id'], ['app_user.id']),
        sa.ForeignKeyConstraint(['redeemed_by_user_id'], ['app_user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_legacy_band_claim_token_band_id', 'legacy_band_claim_token', ['band_id'])
    op.create_index(
        'uq_legacy_band_claim_outstanding', 'legacy_band_claim_token', ['band_id', 'purpose'], unique=True,
        sqlite_where=sa.text('invalidated_at IS NULL AND redeemed_at IS NULL'),
        postgresql_where=sa.text('invalidated_at IS NULL AND redeemed_at IS NULL'),
    )


def downgrade():
    if not op.get_context().as_sql:
        count = op.get_bind().execute(sa.text('SELECT count(*) FROM legacy_band_claim_token')).scalar_one()
        if count:
            raise RuntimeError('Legacy ownership claim records exist; downgrade would discard capability history.')
    op.drop_index('uq_legacy_band_claim_outstanding', table_name='legacy_band_claim_token')
    op.drop_index('ix_legacy_band_claim_token_band_id', table_name='legacy_band_claim_token')
    op.drop_table('legacy_band_claim_token')
