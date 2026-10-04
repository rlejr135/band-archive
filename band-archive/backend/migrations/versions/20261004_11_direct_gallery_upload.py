"""Extend direct upload reservations with tenant-scoped gallery results.

Revision ID: 20261004_11
Revises: 20261003_10
"""

from alembic import op
import sqlalchemy as sa


revision = '20261004_11'
down_revision = '20261003_10'
branch_labels = None
depends_on = None


_OLD_KIND = "kind IN ('media', 'personal_log')"
_NEW_KIND = "kind IN ('media', 'personal_log', 'gallery')"
_OLD_TARGET = "(kind = 'media' AND song_id IS NOT NULL AND member_id IS NULL) OR (kind = 'personal_log' AND song_id IS NULL AND rehearsal_id IS NULL AND member_id IS NOT NULL)"
_NEW_TARGET = _OLD_TARGET + " OR (kind = 'gallery' AND song_id IS NULL AND rehearsal_id IS NULL AND member_id IS NULL)"


def _replace_checks(kind, target):
    # PostgreSQL can alter named checks directly.  Keeping the regular DDL
    # path also makes `alembic upgrade --sql` usable: batch recreation needs a
    # reflected table and therefore cannot render in offline mode.
    if op.get_context().dialect.name != 'sqlite':
        op.drop_constraint('ck_direct_upload_session_kind', 'direct_upload_session', type_='check')
        op.drop_constraint('ck_direct_upload_session_target', 'direct_upload_session', type_='check')
        op.create_check_constraint('ck_direct_upload_session_kind', 'direct_upload_session', kind)
        op.create_check_constraint('ck_direct_upload_session_target', 'direct_upload_session', target)
        return
    with op.batch_alter_table('direct_upload_session', recreate='always') as batch:
        batch.drop_constraint('ck_direct_upload_session_kind', type_='check')
        batch.drop_constraint('ck_direct_upload_session_target', type_='check')
        batch.create_check_constraint('ck_direct_upload_session_kind', kind)
        batch.create_check_constraint('ck_direct_upload_session_target', target)


def upgrade():
    with op.batch_alter_table('direct_upload_session') as batch:
        batch.add_column(sa.Column('result_gallery_image_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_direct_upload_session_gallery_image', 'gallery_image', ['result_gallery_image_id'], ['id'])
    _replace_checks(_NEW_KIND, _NEW_TARGET)


def downgrade():
    if not op.get_context().as_sql:
        count = op.get_bind().execute(sa.text("SELECT count(*) FROM direct_upload_session WHERE kind = 'gallery'")).scalar_one()
        if count:
            raise RuntimeError('Gallery direct-upload sessions exist; downgrade would discard their completion state.')
    _replace_checks(_OLD_KIND, _OLD_TARGET)
    with op.batch_alter_table('direct_upload_session') as batch:
        batch.drop_constraint('fk_direct_upload_session_gallery_image', type_='foreignkey')
        batch.drop_column('result_gallery_image_id')
