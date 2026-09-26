"""Tenant-scoped, optional-authenticated v2 reads for songs and rehearsals."""

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.orm import selectinload

from band_access import (
    current_audience, optional_auth, read_scope_for_id, visible_object_or_404,
    visible_query,
)
from errors import NotFoundError
from extensions import db
from models import Band, Comment, Media, Rehearsal, Song
from v2_content_serializers import (
    graph_is_scoped, media_visibility, rehearsal_visibility, scope_with_band_visibility,
    serialize_comment, serialize_media, serialize_processing, serialize_rehearsal,
    serialize_song, song_visibility, visible_to_scope,
)


content_reads_bp = Blueprint('content_reads', __name__, url_prefix='/api/v2/bands/<int:band_id>')

DEFAULT_PAGE_LIMIT = 25
MAX_PAGE_LIMIT = 100
MAX_PAGE_OFFSET = 100_000


@content_reads_bp.before_request
def require_content_read_schema():
    if not current_app.config.get('BAND_API_SCHEMA_READY', False):
        return jsonify({
            'error': 'band_schema_unavailable',
            'message': 'Band API requires the isolated Alembic migration before startup.',
        }), 503


def _scope(band_id):
    scope = read_scope_for_id(band_id, current_audience())
    band = db.session.get(Band, scope.band_id)
    # ``read_scope_for_id`` already proved this record is live.  The extra
    # lookup only supplies the resolved policy to pure serializers.
    return scope_with_band_visibility(scope, band)


def _page_request():
    """Parse a uniform, bounded offset page without silently coercing input."""
    def integer(name, default, maximum):
        raw = request.args.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError
        if value < 0 or value > maximum or (name == 'limit' and value == 0):
            raise ValueError
        return value

    try:
        return integer('limit', DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT), integer('offset', 0, MAX_PAGE_OFFSET)
    except ValueError:
        from errors import ValidationError
        raise ValidationError(
            f'limit must be between 1 and {MAX_PAGE_LIMIT}; offset must be between 0 and {MAX_PAGE_OFFSET}.',
        )


def _page(items, limit, offset):
    """Return deterministic page metadata after fail-closed graph filtering."""
    window = items[offset:offset + limit]
    has_more = len(items) > offset + limit
    return window, {
        'limit': limit,
        'offset': offset,
        'returned': len(window),
        'has_more': has_more,
        'next_offset': offset + limit if has_more else None,
    }


def _song_or_404(song_id, scope):
    song = visible_object_or_404(Song, song_id, scope, parent_visibility=scope.band_visibility)
    if not graph_is_scoped(scope, song) or not visible_to_scope(scope, song_visibility(scope, song)):
        raise NotFoundError('Resource not found')
    return song


def _rehearsal_or_404(rehearsal_id, scope):
    rehearsal = visible_object_or_404(
        Rehearsal, rehearsal_id, scope, parent_visibility=scope.band_visibility,
    )
    if not graph_is_scoped(scope, rehearsal) or not visible_to_scope(scope, rehearsal_visibility(scope, rehearsal)):
        raise NotFoundError('Resource not found')
    return rehearsal


def _media_or_404(media_id, scope):
    media = visible_object_or_404(Media, media_id, scope, parent_visibility=scope.band_visibility)
    # The schema contract prevents cross-band graph edges.  Preserve a
    # fail-closed response if a hand-modified database violates that contract.
    if not graph_is_scoped(scope, media):
        raise NotFoundError('Resource not found')
    if not visible_to_scope(scope, media_visibility(scope, media)):
        raise NotFoundError('Resource not found')
    return media


@content_reads_bp.get('/songs')
@optional_auth
def list_songs(band_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    query = visible_query(
        Song.query.options(
            selectinload(Song.media_files).selectinload(Media.rehearsal),
            selectinload(Song.rehearsals),
        ), Song, scope, parent_visibility=scope.band_visibility,
    )
    songs = [song for song in query.order_by(Song.id.asc()).all()
             if graph_is_scoped(scope, song) and visible_to_scope(scope, song_visibility(scope, song))]
    page, pagination = _page(songs, limit, offset)
    return jsonify({'songs': [serialize_song(scope, song) for song in page], 'pagination': pagination})


@content_reads_bp.get('/songs/<int:song_id>')
@optional_auth
def get_song(band_id, song_id):
    scope = _scope(band_id)
    return jsonify({'song': serialize_song(scope, _song_or_404(song_id, scope))})


@content_reads_bp.get('/songs/<int:song_id>/media')
@optional_auth
def list_song_media(band_id, song_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    song = _song_or_404(song_id, scope)
    query = visible_query(
        Media.query.options(selectinload(Media.rehearsal), selectinload(Media.song).selectinload(Song.rehearsals)),
        Media, scope, parent_visibility=scope.band_visibility,
    ).filter(Media.song_id == song.id)
    media = [item for item in query.order_by(Media.vote_score.desc(), Media.id.asc()).all()
             if graph_is_scoped(scope, item) and visible_to_scope(scope, media_visibility(scope, item))]
    page, pagination = _page(media, limit, offset)
    return jsonify({'media': [serialize_media(scope, item) for item in page], 'pagination': pagination})


@content_reads_bp.get('/rehearsals')
@optional_auth
def list_rehearsals(band_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    query = visible_query(
        Rehearsal.query.options(
            selectinload(Rehearsal.songs),
            selectinload(Rehearsal.media_files).selectinload(Media.song).selectinload(Song.rehearsals),
            selectinload(Rehearsal.media_files).selectinload(Media.rehearsal),
        ), Rehearsal, scope, parent_visibility=scope.band_visibility,
    )
    rehearsals = [item for item in query.order_by(Rehearsal.date.asc(), Rehearsal.id.asc()).all()
                  if graph_is_scoped(scope, item)
                  and visible_to_scope(scope, rehearsal_visibility(scope, item))]
    page, pagination = _page(rehearsals, limit, offset)
    return jsonify({'rehearsals': [serialize_rehearsal(scope, item) for item in page], 'pagination': pagination})


@content_reads_bp.get('/rehearsals/<int:rehearsal_id>')
@optional_auth
def get_rehearsal(band_id, rehearsal_id):
    scope = _scope(band_id)
    return jsonify({'rehearsal': serialize_rehearsal(scope, _rehearsal_or_404(rehearsal_id, scope))})


@content_reads_bp.get('/rehearsals/<int:rehearsal_id>/media')
@optional_auth
def list_rehearsal_media(band_id, rehearsal_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    rehearsal = _rehearsal_or_404(rehearsal_id, scope)
    query = visible_query(
        Media.query.options(selectinload(Media.rehearsal), selectinload(Media.song).selectinload(Song.rehearsals)),
        Media, scope, parent_visibility=scope.band_visibility,
    ).filter(Media.rehearsal_id == rehearsal.id)
    media = [item for item in query.order_by(Media.vote_score.desc(), Media.id.asc()).all()
             if graph_is_scoped(scope, item) and visible_to_scope(scope, media_visibility(scope, item))]
    page, pagination = _page(media, limit, offset)
    return jsonify({'media': [serialize_media(scope, item) for item in page], 'pagination': pagination})


@content_reads_bp.get('/media/<int:media_id>/processing')
@optional_auth
def get_media_processing(band_id, media_id):
    scope = _scope(band_id)
    return jsonify({'processing': serialize_processing(scope, _media_or_404(media_id, scope))})


@content_reads_bp.get('/media/<int:media_id>/comments')
@optional_auth
def get_media_comments(band_id, media_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    media = _media_or_404(media_id, scope)
    visibility = media_visibility(scope, media)
    # One scoped graph query avoids one lazy relationship query per reply.
    comments = Comment.query.filter(
        Comment.band_id == scope.band_id,
        Comment.media_id == media.id,
    ).order_by(Comment.created_at.asc(), Comment.id.asc()).all()
    roots = [item for item in comments if item.parent_id is None]
    page, pagination = _page(roots, limit, offset)
    children = {}
    for item in comments:
        if item.parent_id is not None:
            children.setdefault(item.parent_id, []).append(item)
    return jsonify({'comments': [serialize_comment(scope, item, visibility, children) for item in page],
                    'pagination': pagination})
