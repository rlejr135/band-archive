"""Add the account-owned v2 comment and media-vote write contract.

Revision ID: 20260927_07
Revises: 20260920_06
"""

from alembic import context, op
import sqlalchemy as sa


revision = '20260927_07'
down_revision = '20260920_06'
branch_labels = None
depends_on = None


def _is_sqlite():
    return op.get_bind().dialect.name == 'sqlite'


def _defects(connection):
    """Prove that adding account-vote uniqueness cannot hide bad history."""
    defects = []
    duplicate = connection.execute(sa.text(
        'SELECT media_id, created_by_membership_id FROM media_vote '
        'WHERE created_by_membership_id IS NOT NULL '
        'GROUP BY media_id, created_by_membership_id HAVING count(*) > 1 LIMIT 1'
    )).first()
    if duplicate:
        defects.append('media_vote has duplicate membership votes '
                       f'(media_id={duplicate[0]}, membership_id={duplicate[1]})')
    mismatched = connection.execute(sa.text(
        'SELECT v.id FROM media_vote AS v JOIN media AS m ON m.id = v.media_id '
        'WHERE v.band_id <> m.band_id LIMIT 1'
    )).scalar_one_or_none()
    if mismatched is not None:
        defects.append(f'media_vote.band_id disagrees with media (id={mismatched})')
    mismatched = connection.execute(sa.text(
        'SELECT v.id FROM media_vote AS v JOIN band_membership AS bm '
        'ON bm.id = v.created_by_membership_id '
        'WHERE v.created_by_membership_id IS NOT NULL AND v.band_id <> bm.band_id LIMIT 1'
    )).scalar_one_or_none()
    if mismatched is not None:
        defects.append(f'media_vote creator membership crosses band (id={mismatched})')
    return defects


def _assert_preflight():
    if context.is_offline_mode():
        return
    defects = _defects(op.get_bind())
    if defects:
        raise RuntimeError('Phase 7 content-write contract is not satisfied: ' + '; '.join(defects))


def _downgrade_defects(connection):
    """Return data states that v06 cannot represent without loss.

    Revision 06 declares ``comment.password_hash`` NOT NULL.  Account-owned
    v2 comments deliberately use NULL, so changing the column first would
    either fail part-way through a SQLite batch rebuild or invite an unsafe
    data rewrite.  Do not start *any* downgrade DDL in that case.
    """
    null_id = connection.execute(sa.text(
        'SELECT id FROM comment WHERE password_hash IS NULL LIMIT 1'
    )).scalar_one_or_none()
    return ([f'comment.password_hash is NULL (id={null_id})'] if null_id is not None else [])


def _assert_downgrade_preflight():
    # Offline SQL rendering has no rows to inspect.  Live downgrade commands
    # run this before the first index or table operation.
    if context.is_offline_mode():
        return
    defects = _downgrade_defects(op.get_bind())
    if defects:
        raise RuntimeError('Phase 7 content-write downgrade is not safe: ' + '; '.join(defects))


def upgrade():
    _assert_preflight()
    # Existing v1 values remain unchanged; only v2 rows use NULL here.
    if _is_sqlite():
        with op.batch_alter_table('comment', recreate='always') as batch:
            batch.alter_column('password_hash', existing_type=sa.String(length=200), nullable=True)
    else:
        op.alter_column('comment', 'password_hash', existing_type=sa.String(length=200), nullable=True)
    op.create_index(
        'uq_media_vote_membership', 'media_vote', ['media_id', 'created_by_membership_id'], unique=True,
        sqlite_where=sa.text('created_by_membership_id IS NOT NULL'),
        postgresql_where=sa.text('created_by_membership_id IS NOT NULL'),
    )


def downgrade():
    _assert_downgrade_preflight()
    op.drop_index('uq_media_vote_membership', table_name='media_vote')
    if _is_sqlite():
        with op.batch_alter_table('comment', recreate='always') as batch:
            batch.alter_column('password_hash', existing_type=sa.String(length=200), nullable=False)
    else:
        op.alter_column('comment', 'password_hash', existing_type=sa.String(length=200), nullable=False)
