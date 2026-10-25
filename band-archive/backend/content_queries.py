"""Reusable band-scoped SQL query builders for content reads and writes."""

from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import selectinload

from band_access import visible_query
from band_content_predicates import _creator_is_scoped_sql, _not_pending_delete
from models import Announcement, GalleryImage, Media, Rehearsal, Song, rehearsal_songs


def _song_query(scope, *, with_rehearsals=True):
    """Return the common visible song graph used by lists and summaries."""
    query = Song.query.options(selectinload(Song.rehearsals)) if with_rehearsals else Song.query
    return visible_query(
        query, Song, scope,
        parent_visibility=scope.band_visibility,
    ).filter(*_song_graph_sql(scope), _creator_is_scoped_sql(Song, scope),
             _not_pending_delete(Song, 'song'))


def _song_graph_sql(scope):
    """SQL equivalent of the serializer's corrupt-graph fail-closed guard."""
    foreign_media = exists().where(and_(Media.song_id == Song.id, Media.band_id != scope.band_id))
    foreign_song_rehearsal = exists().where(and_(
        rehearsal_songs.c.song_id == Song.id,
        rehearsal_songs.c.rehearsal_id == Rehearsal.id,
        Rehearsal.band_id != scope.band_id,
    ))
    foreign_media_rehearsal = exists().where(and_(
        Media.song_id == Song.id,
        Media.rehearsal_id.is_not(None),
        Media.rehearsal_id == Rehearsal.id,
        Rehearsal.band_id != scope.band_id,
    ))
    return ~foreign_media, ~foreign_song_rehearsal, ~foreign_media_rehearsal


def _media_playable_sql():
    def present(column):
        return func.nullif(func.trim(column), '').is_not(None)

    playable_audio = and_(
        Media.file_type == 'audio', Media.original_pruned_at.is_(None),
        or_(present(Media.object_key), present(Media.filename)),
    )
    playable_video = and_(
        Media.file_type == 'video',
        or_(
            and_(Media.original_pruned_at.is_(None),
                 or_(present(Media.object_key), present(Media.filename))),
            present(Media.video_720_object_key), present(Media.video_720_filename),
        ),
    )
    return or_(playable_audio, playable_video)


def _media_pending_deletes_sql():
    pending_direct_rehearsal = exists().where(and_(
        Rehearsal.id == Media.rehearsal_id, Rehearsal.band_id == Media.band_id,
        ~_not_pending_delete(Rehearsal, 'rehearsal'),
    ))
    pending_linked_rehearsal = exists().where(and_(
        rehearsal_songs.c.song_id == Media.song_id,
        rehearsal_songs.c.rehearsal_id == Rehearsal.id,
        Rehearsal.band_id == Media.band_id,
        ~_not_pending_delete(Rehearsal, 'rehearsal'),
    ))
    song_not_pending_delete = exists().where(and_(
        Song.id == Media.song_id, Song.band_id == Media.band_id,
        _not_pending_delete(Song, 'song'),
    ))
    return (
        _not_pending_delete(Media, 'media'), song_not_pending_delete,
        ~pending_direct_rehearsal, ~pending_linked_rehearsal,
    )


def _gallery_query(scope):
    return visible_query(GalleryImage.query, GalleryImage, scope,
                         parent_visibility=scope.band_visibility).filter(
                             _creator_is_scoped_sql(GalleryImage, scope),
                             _not_pending_delete(GalleryImage, 'gallery'),
                         )


def _featured_gallery_rows_query(scope):
    """Inspect the complete same-band featured set before audience filtering.

    The uniqueness index normally guarantees one row.  If an operator or a
    damaged restore bypasses it, a visitor must not get a misleading public
    row while a member sees a different one.  This detector intentionally
    has no visibility/creator predicates; it runs before any serialization or
    signing and makes a corrupt singleton fail closed for every audience.
    """
    return GalleryImage.query.filter(
        GalleryImage.band_id == scope.band_id,
        GalleryImage.is_featured.is_(True),
        _not_pending_delete(GalleryImage, 'gallery'),
    ).order_by(GalleryImage.id.asc()).limit(2)


def _announcement_query(scope):
    return visible_query(Announcement.query, Announcement, scope,
                         parent_visibility=scope.band_visibility).filter(
                             _creator_is_scoped_sql(Announcement, scope),
                             _not_pending_delete(Announcement, 'announcement'),
                         )
