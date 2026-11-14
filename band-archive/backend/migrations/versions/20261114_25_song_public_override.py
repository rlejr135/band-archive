"""Record explicit song publication for the public archive.

Revision ID: 20261114_25
Revises: 20261024_24
"""
from importlib import import_module

from alembic import op
import sqlalchemy as sa


revision = '20261114_25'
down_revision = '20261024_24'
branch_labels = None
depends_on = None


def _assert_private(bind):
    security = import_module('migrations.versions.20261019_20_reusable_invite_links')
    security._assert_private(bind, (*security.PREVIOUS_TABLES, 'band_invite_link',
                                    'invite_resume_context', 'content_restriction'))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if op.get_context().as_sql:
            raise RuntimeError('Song public override migration requires a live PostgreSQL connection.')
        timeouts = import_module('migrations.versions.20261023_23_admission_positions_rehearsal_type')
        timeouts._bound_timeouts(bind)
        _assert_private(bind)
    elif bind.dialect.name != 'sqlite':
        raise RuntimeError('Song public override migration supports PostgreSQL and SQLite only.')
    op.add_column('song', sa.Column('public_override', sa.Boolean(), nullable=False,
                                    server_default=sa.false()))
    if bind.dialect.name == 'postgresql':
        _assert_private(bind)


def downgrade():
    with op.batch_alter_table('song') as batch:
        batch.drop_column('public_override')
