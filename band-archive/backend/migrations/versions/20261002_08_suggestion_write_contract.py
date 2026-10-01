"""Add account-scoped song-suggestion votes for the Phase 7 write API.

Revision ID: 20261002_08
Revises: 20260927_07
"""

from alembic import context, op
import sqlalchemy as sa


revision = '20261002_08'
down_revision = '20260927_07'
branch_labels = None
depends_on = None


def _defects(connection):
    """Fail before DDL if the existing queue cannot be safely trusted.

    The new vote table does not rewrite queue values, but its v2 operations
    rely on these tenant/creator relations.  Refusing an inconsistent source
    prevents a migration from making a corrupted queue appear supported.
    """
    defects = []
    row = connection.execute(sa.text(
        'SELECT s.id FROM song_suggestion AS s '
        'LEFT JOIN band AS b ON b.id = s.band_id '
        'WHERE b.id IS NULL LIMIT 1'
    )).scalar_one_or_none()
    if row is not None:
        defects.append(f'song_suggestion references missing band (id={row})')
    row = connection.execute(sa.text(
        'SELECT s.id FROM song_suggestion AS s LEFT JOIN band_membership AS bm '
        'ON bm.id = s.created_by_membership_id '
        'WHERE s.created_by_membership_id IS NOT NULL '
        'AND (bm.id IS NULL OR s.band_id <> bm.band_id) LIMIT 1'
    )).scalar_one_or_none()
    if row is not None:
        defects.append(f'song_suggestion creator membership crosses band (id={row})')
    # A partially applied/manual table is not a supported state, but inspect
    # it before any DDL so an operator gets a precise corruption diagnosis
    # instead of an accidental uniqueness/index failure.
    if sa.inspect(connection).has_table('song_suggestion_vote'):
        row = connection.execute(sa.text(
            'SELECT v.id FROM song_suggestion_vote AS v '
            'LEFT JOIN song_suggestion AS s ON s.id = v.suggestion_id '
            'LEFT JOIN app_user AS u ON u.id = v.voter_user_id '
            'LEFT JOIN band_membership AS bm ON bm.id = v.created_by_membership_id '
            'WHERE s.id IS NULL '
            'OR (v.voter_user_id IS NOT NULL AND u.id IS NULL) '
            'OR (v.created_by_membership_id IS NOT NULL AND bm.id IS NULL) '
            'OR (s.id IS NOT NULL AND v.band_id <> s.band_id) '
            'OR (v.voter_user_id IS NOT NULL AND bm.id IS NOT NULL AND bm.user_id <> v.voter_user_id) '
            'OR (bm.id IS NOT NULL AND s.id IS NOT NULL AND bm.band_id <> s.band_id) LIMIT 1'
        )).scalar_one_or_none()
        if row is not None:
            defects.append(f'song_suggestion_vote has invalid tenant or actor relation (id={row})')
        defects.append('song_suggestion_vote already exists before revision is recorded')
    return defects


def _assert_preflight():
    if context.is_offline_mode():
        return
    defects = _defects(op.get_bind())
    if defects:
        raise RuntimeError('Suggestion write contract is not satisfied: ' + '; '.join(defects))


def _assert_downgrade_preflight():
    if context.is_offline_mode():
        return
    count = op.get_bind().execute(sa.text('SELECT count(*) FROM song_suggestion_vote')).scalar_one()
    if count:
        raise RuntimeError('Suggestion write downgrade is not safe: song_suggestion_vote contains data.')


def upgrade():
    _assert_preflight()
    op.create_table(
        'song_suggestion_vote',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('band_id', sa.Integer(), nullable=False),
        sa.Column('suggestion_id', sa.Integer(), nullable=False),
        sa.Column('voter_user_id', sa.Integer(), nullable=True),
        sa.Column('created_by_membership_id', sa.Integer(), nullable=True),
        sa.Column('is_legacy', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('value', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('value IN (-1, 1)', name='ck_song_suggestion_vote_value'),
        sa.ForeignKeyConstraint(['band_id'], ['band.id']),
        sa.ForeignKeyConstraint(['suggestion_id'], ['song_suggestion.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['voter_user_id'], ['app_user.id']),
        sa.ForeignKeyConstraint(['created_by_membership_id'], ['band_membership.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_song_suggestion_vote_band_id', 'song_suggestion_vote', ['band_id'])
    op.create_index('ix_song_suggestion_vote_suggestion_id', 'song_suggestion_vote', ['suggestion_id'])
    op.create_index('ix_song_suggestion_vote_voter_user_id', 'song_suggestion_vote', ['voter_user_id'])
    op.create_index('ix_song_suggestion_vote_created_by_membership_id', 'song_suggestion_vote',
                    ['created_by_membership_id'])
    op.create_index(
        'uq_song_suggestion_vote_user', 'song_suggestion_vote',
        ['suggestion_id', 'voter_user_id'], unique=True,
        sqlite_where=sa.text('voter_user_id IS NOT NULL'),
        postgresql_where=sa.text('voter_user_id IS NOT NULL'),
    )


def downgrade():
    _assert_downgrade_preflight()
    op.drop_index('uq_song_suggestion_vote_user', table_name='song_suggestion_vote')
    op.drop_index('ix_song_suggestion_vote_created_by_membership_id', table_name='song_suggestion_vote')
    op.drop_index('ix_song_suggestion_vote_voter_user_id', table_name='song_suggestion_vote')
    op.drop_index('ix_song_suggestion_vote_suggestion_id', table_name='song_suggestion_vote')
    op.drop_index('ix_song_suggestion_vote_band_id', table_name='song_suggestion_vote')
    op.drop_table('song_suggestion_vote')
