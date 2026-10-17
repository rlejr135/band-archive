"""Band-local linked profiles with optional introduction and positions.

Revision ID: 20261017_18
Revises: 20261017_17
"""
from alembic import op
import sqlalchemy as sa

revision = '20261017_18'
down_revision = '20261017_17'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name not in {'sqlite', 'postgresql'}:
        raise RuntimeError('Member profiles support PostgreSQL and SQLite only.')
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('LOCK TABLE public.member IN ACCESS EXCLUSIVE MODE')
    if bind.execute(sa.text('SELECT band_id, linked_user_id FROM member WHERE linked_user_id IS NOT NULL '
                            'GROUP BY band_id, linked_user_id HAVING count(*) > 1 LIMIT 1')).first():
        raise RuntimeError('Duplicate linked member profiles must be resolved explicitly before migration.')
    with op.batch_alter_table('member') as batch:
        batch.add_column(sa.Column('biography', sa.Text(), nullable=False, server_default=sa.text("''")))
        batch.add_column(sa.Column('positions_json', sa.Text(), nullable=False, server_default='[]'))
        batch.add_column(sa.Column('custom_position', sa.String(50), nullable=False, server_default=sa.text("''")))
        batch.add_column(sa.Column('profile_revision', sa.Integer(), nullable=False, server_default='0'))
        batch.create_unique_constraint('uq_member_band_linked_user', ['band_id', 'linked_user_id'])
        batch.create_check_constraint('ck_member_profile_revision', 'profile_revision >= 0')


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('LOCK TABLE public.member IN ACCESS EXCLUSIVE MODE')
    if bind.execute(sa.text("SELECT count(*) FROM member WHERE biography<>'' OR positions_json<>'[]' "
                            "OR custom_position<>'' OR profile_revision<>0")).scalar_one():
        raise RuntimeError('Refusing to discard member introductions, positions or revision history.')
    with op.batch_alter_table('member') as batch:
        batch.drop_constraint('ck_member_profile_revision', type_='check')
        batch.drop_constraint('uq_member_band_linked_user', type_='unique')
        for name in ('profile_revision', 'custom_position', 'positions_json', 'biography'):
            batch.drop_column(name)
