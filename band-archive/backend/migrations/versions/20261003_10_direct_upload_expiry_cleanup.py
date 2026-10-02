"""Require cleanup before direct-upload quota release.

Revision ID: 20261003_10
Revises: 20261002_09
"""

from alembic import op
import sqlalchemy as sa


revision = '20261003_10'
down_revision = '20261002_09'
branch_labels = None
depends_on = None


_OLD_STATUS = "status IN ('initiated', 'completing', 'completed', 'expired', 'failed')"
_NEW_STATUS = "status IN ('initiated', 'completing', 'expiring', 'completed', 'expired', 'failed')"


def _is_sqlite():
    return op.get_bind().dialect.name == 'sqlite'


def _replace_status_check(old, new):
    if _is_sqlite():
        with op.batch_alter_table('direct_upload_session', recreate='always') as batch:
            batch.drop_constraint('ck_direct_upload_session_status', type_='check')
            batch.create_check_constraint('ck_direct_upload_session_status', new)
    else:
        op.drop_constraint('ck_direct_upload_session_status', 'direct_upload_session', type_='check')
        op.create_check_constraint('ck_direct_upload_session_status', 'direct_upload_session', new)


def upgrade():
    _replace_status_check(_OLD_STATUS, _NEW_STATUS)


def _assert_downgrade_safe():
    """Do not recreate the v09 check while an in-flight cleanup remains."""
    if op.get_context().as_sql:
        return
    count = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM direct_upload_session WHERE status = 'expiring'"
    )).scalar_one()
    if count:
        raise RuntimeError(
            'Direct-upload expiry cleanup is in progress; retry downgrade after it completes.'
        )


def downgrade():
    _assert_downgrade_safe()
    _replace_status_check(_NEW_STATUS, _OLD_STATUS)
