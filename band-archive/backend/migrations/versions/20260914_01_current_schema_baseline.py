"""Current SQLite schema baseline before the account/multiband transition.

Revision ID: 20260914_01
Revises:
Create Date: 2026-09-14

This is intentionally an explicit snapshot, rather than metadata.create_all,
so future model changes cannot alter how a fresh baseline database is built.
"""

from alembic import op
import sqlalchemy as sa


revision = '20260914_01'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'announcement',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'gallery_image',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=200), nullable=False),
        sa.Column('original_filename', sa.String(length=200), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('is_featured', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'member',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('instrument', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'rehearsal',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('time', sa.String(length=20), nullable=True),
        sa.Column('location', sa.String(length=200), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('color', sa.String(length=7), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'song',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('artist', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('chords', sa.Text(), nullable=True),
        sa.Column('link', sa.String(length=200), nullable=True),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('genre', sa.String(length=50), nullable=True),
        sa.Column('difficulty', sa.Integer(), nullable=True),
        sa.Column('sheet_music', sa.String(length=200), nullable=True),
        sa.Column('upvote_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('downvote_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('vote_score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'song_suggestion',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('artist', sa.String(length=100), nullable=False),
        sa.Column('link', sa.String(length=500), nullable=False),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('thumbs_up', sa.Integer(), nullable=True),
        sa.Column('thumbs_down', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'media',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('song_id', sa.Integer(), nullable=False),
        sa.Column('rehearsal_id', sa.Integer(), nullable=True),
        sa.Column('filename', sa.String(length=200), nullable=False),
        sa.Column('original_filename', sa.String(length=200), nullable=True),
        sa.Column('file_type', sa.String(length=20), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('transcoding_status', sa.String(length=20), nullable=False),
        sa.Column('audio_filename', sa.String(length=200), nullable=True),
        sa.Column('processing_error', sa.String(length=500), nullable=True),
        sa.Column('processing_started_at', sa.DateTime(), nullable=True),
        sa.Column('processing_completed_at', sa.DateTime(), nullable=True),
        sa.Column('processing_attempts', sa.Integer(), nullable=False),
        sa.Column('processing_heartbeat_at', sa.DateTime(), nullable=True),
        sa.Column('video_720_filename', sa.String(length=300), nullable=True),
        sa.Column('video_720_source_etag', sa.String(length=200), nullable=True),
        sa.Column('video_720_profile', sa.String(length=100), nullable=True),
        sa.Column('video_720_completed_at', sa.DateTime(), nullable=True),
        sa.Column('original_pruned_at', sa.DateTime(), nullable=True),
        sa.Column('is_featured', sa.Boolean(), nullable=True),
        sa.Column('upvote_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('downvote_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('vote_score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['rehearsal_id'], ['rehearsal.id']),
        sa.ForeignKeyConstraint(['song_id'], ['song.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'personal_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('filename', sa.String(length=200), nullable=False),
        sa.Column('original_filename', sa.String(length=200), nullable=True),
        sa.Column('file_type', sa.String(length=20), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('transcoding_status', sa.String(length=20), nullable=False),
        sa.Column('audio_filename', sa.String(length=200), nullable=True),
        sa.Column('processing_error', sa.String(length=500), nullable=True),
        sa.Column('processing_started_at', sa.DateTime(), nullable=True),
        sa.Column('processing_completed_at', sa.DateTime(), nullable=True),
        sa.Column('processing_attempts', sa.Integer(), nullable=False),
        sa.Column('processing_heartbeat_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['member_id'], ['member.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'rehearsal_songs',
        sa.Column('rehearsal_id', sa.Integer(), nullable=False),
        sa.Column('song_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['rehearsal_id'], ['rehearsal.id']),
        sa.ForeignKeyConstraint(['song_id'], ['song.id']),
        sa.PrimaryKeyConstraint('rehearsal_id', 'song_id'),
    )
    op.create_table(
        'song_vote',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('song_id', sa.Integer(), nullable=False),
        sa.Column('voter_hash', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('value IN (-1, 1)', name='ck_song_vote_value'),
        sa.ForeignKeyConstraint(['song_id'], ['song.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('song_id', 'voter_hash', name='uq_song_vote_voter'),
    )
    op.create_index('ix_song_vote_song_id', 'song_vote', ['song_id'], unique=False)
    op.create_table(
        'comment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('media_id', sa.Integer(), nullable=True),
        sa.Column('personal_log_id', sa.Integer(), nullable=True),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('author', sa.String(length=50), nullable=False),
        sa.Column('password_hash', sa.String(length=200), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('timestamp_seconds', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['media_id'], ['media.id']),
        sa.ForeignKeyConstraint(['parent_id'], ['comment.id']),
        sa.ForeignKeyConstraint(['personal_log_id'], ['personal_log.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'media_vote',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('media_id', sa.Integer(), nullable=False),
        sa.Column('voter_hash', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('value IN (-1, 1)', name='ck_media_vote_value'),
        sa.ForeignKeyConstraint(['media_id'], ['media.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('media_id', 'voter_hash', name='uq_media_vote_voter'),
    )
    op.create_index('ix_media_vote_media_id', 'media_vote', ['media_id'], unique=False)
    op.create_table(
        'multipart_upload_session',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('r2_upload_id', sa.String(length=200), nullable=False),
        sa.Column('object_key', sa.String(length=300), nullable=False),
        sa.Column('original_filename', sa.String(length=200), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('declared_bytes', sa.BigInteger(), nullable=False),
        sa.Column('song_id', sa.Integer(), nullable=False),
        sa.Column('rehearsal_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('capability_token_hash', sa.String(length=255), nullable=True),
        sa.Column('media_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('completion_started_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['media_id'], ['media.id']),
        sa.ForeignKeyConstraint(['rehearsal_id'], ['rehearsal.id']),
        sa.ForeignKeyConstraint(['song_id'], ['song.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'personal_log_multipart_upload_session',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('r2_upload_id', sa.String(length=200), nullable=False),
        sa.Column('object_key', sa.String(length=300), nullable=False),
        sa.Column('original_filename', sa.String(length=200), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('declared_bytes', sa.BigInteger(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('capability_token_hash', sa.String(length=255), nullable=True),
        sa.Column('personal_log_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('completion_started_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['member_id'], ['member.id']),
        sa.ForeignKeyConstraint(['personal_log_id'], ['personal_log.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'multipart_upload_part',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('part_number', sa.Integer(), nullable=False),
        sa.Column('etag', sa.String(length=500), nullable=True),
        sa.Column('uploaded_bytes', sa.BigInteger(), nullable=True),
        sa.Column('checksum', sa.String(length=200), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['multipart_upload_session.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'part_number', name='uq_multipart_session_part'),
    )
    op.create_table(
        'personal_log_multipart_upload_part',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('part_number', sa.Integer(), nullable=False),
        sa.Column('etag', sa.String(length=500), nullable=True),
        sa.Column('uploaded_bytes', sa.BigInteger(), nullable=True),
        sa.Column('checksum', sa.String(length=200), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['personal_log_multipart_upload_session.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'part_number', name='uq_personal_log_multipart_session_part'),
    )


def downgrade():
    op.drop_table('personal_log_multipart_upload_part')
    op.drop_table('multipart_upload_part')
    op.drop_table('personal_log_multipart_upload_session')
    op.drop_table('multipart_upload_session')
    op.drop_index('ix_media_vote_media_id', table_name='media_vote')
    op.drop_table('media_vote')
    op.drop_table('comment')
    op.drop_index('ix_song_vote_song_id', table_name='song_vote')
    op.drop_table('song_vote')
    op.drop_table('rehearsal_songs')
    op.drop_table('personal_log')
    op.drop_table('media')
    op.drop_table('song_suggestion')
    op.drop_table('song')
    op.drop_table('rehearsal')
    op.drop_table('member')
    op.drop_table('gallery_image')
    op.drop_table('announcement')
