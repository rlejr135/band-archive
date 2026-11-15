"""Store public archive likes separately from band media votes.

Revision ID: 20261115_26
Revises: 20261114_25
"""
from importlib import import_module

from alembic import op
import sqlalchemy as sa


revision = '20261115_26'
down_revision = '20261114_25'
branch_labels = None
depends_on = None

TABLE = 'public_archive_like'


def upgrade():
    bind = op.get_bind()
    postgres = bind.dialect.name == 'postgresql'
    if postgres:
        if op.get_context().as_sql:
            raise RuntimeError('Public archive likes require a live PostgreSQL migration.')
        security = import_module('migrations.versions.20261019_20_reusable_invite_links')
        timeouts = import_module('migrations.versions.20261023_23_admission_positions_rehearsal_type')
        timeouts._bound_timeouts(bind)
        previous = (*security.PREVIOUS_TABLES, 'band_invite_link',
                    'invite_resume_context', 'content_restriction')
        security._assert_private(bind, previous)
    elif bind.dialect.name != 'sqlite':
        raise RuntimeError('Public archive likes support PostgreSQL and SQLite only.')

    op.create_table(
        TABLE,
        sa.Column('media_id', sa.Integer(), sa.ForeignKey('media.id', ondelete='CASCADE'),
                  primary_key=True, autoincrement=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('app_user.id', ondelete='CASCADE'),
                  primary_key=True, autoincrement=False),
    )
    op.create_index('ix_public_archive_like_user_id', TABLE, ['user_id'])

    if postgres:
        bind.exec_driver_sql('ALTER TABLE public.public_archive_like ENABLE ROW LEVEL SECURITY')
        bind.exec_driver_sql('REVOKE ALL PRIVILEGES ON TABLE public.public_archive_like FROM PUBLIC')
        roles = bind.execute(sa.text(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role')"
        )).scalars()
        for role in roles:
            bind.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON TABLE public.public_archive_like FROM "{role}"')
        security._assert_private(bind, (*previous, TABLE))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('LOCK TABLE public.public_archive_like IN ACCESS EXCLUSIVE MODE')
    if bind.execute(sa.text('SELECT count(*) FROM public_archive_like')).scalar_one():
        raise RuntimeError('Refusing to discard public archive likes.')
    op.drop_table(TABLE)
