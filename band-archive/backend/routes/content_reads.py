"""Tenant-scoped, optional-authenticated v2 reads for songs and rehearsals."""

from flask import Blueprint, current_app, jsonify
from sqlalchemy.orm import selectinload

from band_access import (
    current_audience, optional_auth, read_scope_for_id, visible_object_or_404,
    visible_query,
)
from errors import NotFoundError
from extensions import db
from models import Band, Comment, Media, Rehearsal, Song
from v2_content_serializers import (
    media_visibility, rehearsal_visibility, scope_with_band_visibility,
    serialize_comment, serialize_media, serialize_processing, serialize_rehearsal,
    serialize_song, song_visibility, visible_to_scope,
)


content_reads_bp = Blueprint('content_reads', __name__, url_prefix='/api/v2/bands/<int:band_id>')


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


def _song_or_404(song_id, scope):
    song = visible_object_or_404(Song, song_id, scope, parent_visibility=scope.band_visibility)
    if not visible_to_scope(scope, song_visibility(scope, song)):
        raise NotFoundError('Resource not found')
    return song


def _rehearsal_or_404(rehearsal_id, scope):
    rehearsal = visible_object_or_404(
        Rehearsal, rehearsal_id, scope, parent_visibility=scope.band_visibility,
    )
    if not visible_to_scope(scope, rehearsal_visibility(scope, rehearsal)):
        raise NotFoundError('Resource not found')
    return rehearsal


def _media_or_404(media_id, scope):
    media = visible_object_or_404(Media, media_id, scope, parent_visibility=scope.band_visibility)
    # The schema contract prevents cross-band graph edges.  Preserve a
    # fail-closed response if a hand-modified database violates that contract.
    if media.song is None or media.song.band_id != scope.band_id:
        raise NotFoundError('Resource not found')
    if media.rehearsal is not None and media.rehearsal.band_id != scope.band_id:
        raise NotFoundError('Resource not found')
    if not visible_to_scope(scope, media_visibility(scope, media)):
        raise NotFoundError('Resource not found')
    return media


@content_reads_bp.get('/songs')
@optional_auth
def list_songs(band_id):
    scope = _scope(band_id)
    query = visible_query(
        Song.query.options(
            selectinload(Song.media_files).selectinload(Media.rehearsal),
            selectinload(Song.rehearsals),
        ), Song, scope, parent_visibility=scope.band_visibility,
    )
    songs = query.order_by(Song.id.asc()).all()
    return jsonify({'songs': [serialize_song(scope, song) for song in songs
                              if visible_to_scope(scope, song_visibility(scope, song))]})


@content_reads_bp.get('/songs/<int:song_id>')
@optional_auth
def get_song(band_id, song_id):
    scope = _scope(band_id)
    return jsonify({'song': serialize_song(scope, _song_or_404(song_id, scope))})


@content_reads_bp.get('/songs/<int:song_id>/media')
@optional_auth
def list_song_media(band_id, song_id):
    scope = _scope(band_id)
    song = _song_or_404(song_id, scope)
    query = visible_query(
        Media.query.options(selectinload(Media.rehearsal), selectinload(Media.song).selectinload(Song.rehearsals)),
        Media, scope, parent_visibility=scope.band_visibility,
    ).filter(Media.song_id == song.id)
    media = query.order_by(Media.vote_score.desc(), Media.id.asc()).all()
    return jsonify({'media': [serialize_media(scope, item) for item in media
                              if visible_to_scope(scope, media_visibility(scope, item))]})


@content_reads_bp.get('/rehearsals')
@optional_auth
def list_rehearsals(band_id):
    scope = _scope(band_id)
    query = visible_query(
        Rehearsal.query.options(
            selectinload(Rehearsal.songs),
            selectinload(Rehearsal.media_files).selectinload(Media.song).selectinload(Song.rehearsals),
            selectinload(Rehearsal.media_files).selectinload(Media.rehearsal),
        ), Rehearsal, scope, parent_visibility=scope.band_visibility,
    )
    rehearsals = query.order_by(Rehearsal.date.asc(), Rehearsal.id.asc()).all()
    return jsonify({'rehearsals': [serialize_rehearsal(scope, item) for item in rehearsals
                                   if visible_to_scope(scope, rehearsal_visibility(scope, item))]})


@content_reads_bp.get('/rehearsals/<int:rehearsal_id>')
@optional_auth
def get_rehearsal(band_id, rehearsal_id):
    scope = _scope(band_id)
    return jsonify({'rehearsal': serialize_rehearsal(scope, _rehearsal_or_404(rehearsal_id, scope))})


@content_reads_bp.get('/rehearsals/<int:rehearsal_id>/media')
@optional_auth
def list_rehearsal_media(band_id, rehearsal_id):
    scope = _scope(band_id)
    rehearsal = _rehearsal_or_404(rehearsal_id, scope)
    query = visible_query(
        Media.query.options(selectinload(Media.rehearsal), selectinload(Media.song).selectinload(Song.rehearsals)),
        Media, scope, parent_visibility=scope.band_visibility,
    ).filter(Media.rehearsal_id == rehearsal.id)
    media = query.order_by(Media.vote_score.desc(), Media.id.asc()).all()
    return jsonify({'media': [serialize_media(scope, item) for item in media
                              if visible_to_scope(scope, media_visibility(scope, item))]})


@content_reads_bp.get('/media/<int:media_id>/processing')
@optional_auth
def get_media_processing(band_id, media_id):
    scope = _scope(band_id)
    return jsonify({'processing': serialize_processing(scope, _media_or_404(media_id, scope))})


@content_reads_bp.get('/media/<int:media_id>/comments')
@optional_auth
def get_media_comments(band_id, media_id):
    scope = _scope(band_id)
    media = _media_or_404(media_id, scope)
    visibility = media_visibility(scope, media)
    comments = Comment.query.filter(
        Comment.band_id == scope.band_id,
        Comment.media_id == media.id,
        Comment.parent_id.is_(None),
    ).order_by(Comment.created_at.asc(), Comment.id.asc()).all()
    return jsonify({'comments': [serialize_comment(scope, item, visibility) for item in comments]})
