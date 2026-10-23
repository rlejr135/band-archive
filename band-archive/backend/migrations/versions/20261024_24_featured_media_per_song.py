"""Enforce at most one representative media row per band song.

Revision ID: 20261024_24
Revises: 20261023_23
"""
import importlib.util
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision = '20261024_24'
down_revision = '20261023_23'
branch_labels = None
depends_on = None


INDEX_NAME = 'uq_media_featured_band_song'


def _security():
    path = Path(__file__).with_name('20261019_20_reusable_invite_links.py')
    spec = importlib.util.spec_from_file_location('featured_media_security_revision20', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _preflight(bind):
    duplicate_groups = bind.execute(sa.text(
        'SELECT count(*) FROM (SELECT band_id, song_id FROM public.media '
        'WHERE is_featured IS TRUE GROUP BY band_id, song_id HAVING count(*) > 1) duplicates'
    )).scalar_one()
    invalid_graph = bind.execute(sa.text(
        'SELECT count(*) FROM public.media m '
        'LEFT JOIN public.band b ON b.id = m.band_id '
        'LEFT JOIN public.song s ON s.id = m.song_id '
        'LEFT JOIN public.rehearsal r ON r.id = m.rehearsal_id '
        'WHERE m.is_featured IS TRUE AND '
        '(m.band_id IS NULL OR b.id IS NULL OR m.song_id IS NULL OR s.id IS NULL '
        'OR s.band_id IS DISTINCT FROM m.band_id '
        'OR (m.rehearsal_id IS NOT NULL AND (r.id IS NULL OR r.band_id IS DISTINCT FROM m.band_id)) '
        'OR EXISTS (SELECT 1 FROM public.rehearsal_songs rs '
        'JOIN public.rehearsal linked ON linked.id = rs.rehearsal_id '
        'WHERE rs.song_id = m.song_id AND linked.band_id IS DISTINCT FROM m.band_id))'
    )).scalar_one()
    restricted_or_deleting = bind.execute(sa.text(
        "SELECT count(*) FROM public.media m WHERE m.is_featured IS TRUE AND ("
        "EXISTS (SELECT 1 FROM public.content_restriction cr WHERE cr.band_id = m.band_id "
        "AND cr.state = 'active' AND ((cr.resource_type = 'media' AND cr.resource_id = m.id) OR "
        "(cr.resource_type = 'song' AND cr.resource_id = m.song_id) OR "
        "(cr.resource_type = 'rehearsal' AND cr.resource_id = m.rehearsal_id))) OR "
        "EXISTS (SELECT 1 FROM public.r2_deletion_task dt WHERE dt.band_id = m.band_id "
        "AND dt.status <> 'completed' AND ((dt.resource_type = 'media' AND dt.resource_id = m.id) OR "
        "(dt.resource_type = 'song' AND dt.resource_id = m.song_id) OR "
        "(dt.resource_type = 'rehearsal' AND dt.resource_id = m.rehearsal_id))) OR "
        "EXISTS (SELECT 1 FROM public.rehearsal_songs rs JOIN public.r2_deletion_task dt "
        "ON dt.resource_type = 'rehearsal' AND dt.resource_id = rs.rehearsal_id "
        "AND dt.band_id = m.band_id AND dt.status <> 'completed' "
        "WHERE rs.song_id = m.song_id))"
    )).scalar_one()
    unplayable = bind.execute(sa.text(
        "SELECT count(*) FROM public.media m WHERE m.is_featured IS TRUE AND NOT COALESCE(("
        "(m.file_type = 'audio' AND m.original_pruned_at IS NULL AND "
        "(NULLIF(btrim(m.object_key), '') IS NOT NULL OR NULLIF(btrim(m.filename), '') IS NOT NULL)) OR "
        "(m.file_type = 'video' AND ((m.video_720_object_key IS NOT NULL AND btrim(m.video_720_object_key) <> '') "
        "OR (m.video_720_filename IS NOT NULL AND btrim(m.video_720_filename) <> '') OR "
        "(m.original_pruned_at IS NULL AND (NULLIF(btrim(m.object_key), '') IS NOT NULL "
        "OR NULLIF(btrim(m.filename), '') IS NOT NULL))))), FALSE)"
    )).scalar_one()
    if duplicate_groups or invalid_graph or restricted_or_deleting or unplayable:
        samples = bind.execute(sa.text(
            'SELECT m.id, m.band_id, m.song_id, m.file_type '
            'FROM public.media m WHERE m.is_featured IS TRUE '
            'ORDER BY m.band_id NULLS FIRST, m.song_id NULLS FIRST, m.id LIMIT 10'
        )).mappings().all()
        sample_text = ', '.join(
            f"id={row['id']},band_id={row['band_id']},song_id={row['song_id']},file_type={row['file_type']}"
            for row in samples
        ) or 'none'
        raise RuntimeError(
            'Featured-media migration preflight refused: '
            f'duplicate_band_song_groups={duplicate_groups}, '
            f'invalid_or_cross_band_parent_rows={invalid_graph}, '
            f'restricted_or_deleting_rows={restricted_or_deleting}, '
            f'unplayable_or_unsupported_rows={unplayable}; '
            f'first_flagged_rows=[{sample_text}]. No existing flags were changed.'
        )


def upgrade():
    bind = op.get_bind()
    security = _security()
    tables = (*security.PREVIOUS_TABLES, 'band_invite_link', 'invite_resume_context',
              'content_restriction', 'band_availability_state', 'band_availability_day',
              'band_personal_schedule_note', 'band_logo')
    if bind.dialect.name == 'postgresql':
        if op.get_context().as_sql:
            raise RuntimeError('Featured-media migration requires a live PostgreSQL connection.')
        security._assert_private(bind, tables)
        bind.exec_driver_sql('LOCK TABLE public.song, public.media IN ACCESS EXCLUSIVE MODE')
        _preflight(bind)
        op.create_index(INDEX_NAME, 'media', ['band_id', 'song_id'], unique=True,
                        postgresql_where=sa.text('is_featured IS TRUE'))
        security._assert_private(bind, tables)
    elif bind.dialect.name == 'sqlite':
        # SQLite's own migration transaction serializes DDL and writes. The
        # same checks run against the exact stamped schema, with only PG's
        # namespace qualifier omitted.
        _preflight_sqlite(bind)
        op.create_index(INDEX_NAME, 'media', ['band_id', 'song_id'], unique=True,
                        sqlite_where=sa.text('is_featured IS TRUE'))
    else:
        raise RuntimeError('Featured-media migration supports PostgreSQL and SQLite only.')


def _preflight_sqlite(bind):
    duplicate_groups = bind.execute(sa.text(
        'SELECT count(*) FROM (SELECT band_id, song_id FROM media '
        'WHERE is_featured IS TRUE GROUP BY band_id, song_id HAVING count(*) > 1) duplicates'
    )).scalar_one()
    invalid_graph = bind.execute(sa.text(
        'SELECT count(*) FROM media m '
        'LEFT JOIN band b ON b.id = m.band_id LEFT JOIN song s ON s.id = m.song_id '
        'LEFT JOIN rehearsal r ON r.id = m.rehearsal_id '
        'WHERE m.is_featured IS TRUE AND '
        '(m.band_id IS NULL OR b.id IS NULL OR m.song_id IS NULL OR s.id IS NULL '
        'OR s.band_id IS NOT m.band_id '
        'OR (m.rehearsal_id IS NOT NULL AND (r.id IS NULL OR r.band_id IS NOT m.band_id)) '
        'OR EXISTS (SELECT 1 FROM rehearsal_songs rs JOIN rehearsal linked ON linked.id = rs.rehearsal_id '
        'WHERE rs.song_id = m.song_id AND linked.band_id IS NOT m.band_id))'
    )).scalar_one()
    restricted_or_deleting = bind.execute(sa.text(
        "SELECT count(*) FROM media m WHERE m.is_featured IS TRUE AND ("
        "EXISTS (SELECT 1 FROM content_restriction cr WHERE cr.band_id = m.band_id "
        "AND cr.state = 'active' AND ((cr.resource_type = 'media' AND cr.resource_id = m.id) OR "
        "(cr.resource_type = 'song' AND cr.resource_id = m.song_id) OR "
        "(cr.resource_type = 'rehearsal' AND cr.resource_id = m.rehearsal_id))) OR "
        "EXISTS (SELECT 1 FROM r2_deletion_task dt WHERE dt.band_id = m.band_id "
        "AND dt.status <> 'completed' AND ((dt.resource_type = 'media' AND dt.resource_id = m.id) OR "
        "(dt.resource_type = 'song' AND dt.resource_id = m.song_id) OR "
        "(dt.resource_type = 'rehearsal' AND dt.resource_id = m.rehearsal_id))) OR "
        "EXISTS (SELECT 1 FROM rehearsal_songs rs JOIN r2_deletion_task dt "
        "ON dt.resource_type = 'rehearsal' AND dt.resource_id = rs.rehearsal_id "
        "AND dt.band_id = m.band_id AND dt.status <> 'completed' WHERE rs.song_id = m.song_id))"
    )).scalar_one()
    unplayable = bind.execute(sa.text(
        "SELECT count(*) FROM media m WHERE m.is_featured IS TRUE AND NOT COALESCE(("
        "(m.file_type = 'audio' AND m.original_pruned_at IS NULL AND "
        "(NULLIF(trim(m.object_key), '') IS NOT NULL OR NULLIF(trim(m.filename), '') IS NOT NULL)) OR "
        "(m.file_type = 'video' AND ((m.video_720_object_key IS NOT NULL AND trim(m.video_720_object_key) <> '') "
        "OR (m.video_720_filename IS NOT NULL AND trim(m.video_720_filename) <> '') OR "
        "(m.original_pruned_at IS NULL AND (NULLIF(trim(m.object_key), '') IS NOT NULL "
        "OR NULLIF(trim(m.filename), '') IS NOT NULL))))), 0)"
    )).scalar_one()
    if duplicate_groups or invalid_graph or restricted_or_deleting or unplayable:
        samples = bind.execute(sa.text(
            'SELECT id, band_id, song_id, file_type FROM media WHERE is_featured IS TRUE '
            'ORDER BY band_id, song_id, id LIMIT 10'
        )).mappings().all()
        sample_text = ', '.join(
            f"id={row['id']},band_id={row['band_id']},song_id={row['song_id']},file_type={row['file_type']}"
            for row in samples
        ) or 'none'
        raise RuntimeError(
            'Featured-media migration preflight refused: '
            f'duplicate_band_song_groups={duplicate_groups}, '
            f'invalid_or_cross_band_parent_rows={invalid_graph}, '
            f'restricted_or_deleting_rows={restricted_or_deleting}, '
            f'unplayable_or_unsupported_rows={unplayable}; '
            f'first_flagged_rows=[{sample_text}]. No existing flags were changed.'
        )


def downgrade():
    op.drop_index(INDEX_NAME, table_name='media')
