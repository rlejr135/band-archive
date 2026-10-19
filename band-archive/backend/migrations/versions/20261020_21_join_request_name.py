"""Add a pending band-local member name, without changing existing profiles.

Revision ID: 20261020_21
Revises: 20261019_20
"""
from alembic import op
import sqlalchemy as sa

revision = '20261020_21'
down_revision = '20261019_20'
branch_labels = None
depends_on = None


def _lock(bind):
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('LOCK TABLE public.band_membership IN ACCESS EXCLUSIVE MODE')
    elif bind.dialect.name != 'sqlite':
        raise RuntimeError('Application names support PostgreSQL and SQLite only.')


def upgrade():
    _lock(op.get_bind())
    op.add_column('band_membership', sa.Column('requested_name', sa.String(100), nullable=True))


def downgrade():
    bind = op.get_bind()
    _lock(bind)
    if bind.execute(sa.text('SELECT count(*) FROM band_membership WHERE requested_name IS NOT NULL')).scalar_one():
        raise RuntimeError('Refusing to discard pending application names.')
    with op.batch_alter_table('band_membership') as batch:
        batch.drop_column('requested_name')
