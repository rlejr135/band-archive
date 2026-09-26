"""Tenant-scoped, optional-authenticated v2 reads for songs and rehearsals."""

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import aliased, selectinload

from band_access import (
    current_audience, optional_auth, read_scope_for_id, visible_object_or_404,
    visible_query,
)
from errors import NotFoundError
from extensions import db
from models import (Announcement, Band, BandMembership, Comment, GalleryImage, Media,
                    Member, PersonalLog, Rehearsal, Song, SongSuggestion, rehearsal_songs)
from v2_content_serializers import (
    graph_is_scoped, media_visibility, rehearsal_visibility, scope_with_band_visibility,
    serialize_comment, serialize_media, serialize_processing, serialize_rehearsal,
    serialize_song, song_visibility, visible_to_scope,
)
from v2_remaining_read_serializers import (
    personal_log_visibility,
    serialize_announcement, serialize_gallery_image, serialize_member,
    serialize_personal_comment, serialize_personal_log, serialize_personal_processing,
    serialize_suggestion,
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


def _page_query(query, order_columns, limit, offset):
    """Count and materialize one SQL-bounded deterministic page."""
    total = query.order_by(None).count()
    items = query.order_by(*order_columns).offset(offset).limit(limit).all()
    has_more = total > offset + len(items)
    return items, {
        'limit': limit,
        'offset': offset,
        'returned': len(items),
        'total': total,
        'has_more': has_more,
        'next_offset': offset + limit if has_more else None,
    }


def _creator_is_scoped_sql(entity, scope):
    """Reject a corrupt cross-band creator pointer without rejecting legacy NULLs."""
    creator_id = getattr(entity, 'created_by_membership_id', None)
    if creator_id is None:
        return True
    return or_(
        creator_id.is_(None),
        exists().where(and_(BandMembership.id == creator_id,
                            BandMembership.band_id == scope.band_id)),
    )


def _roster_visible_or_404(scope):
    band = db.session.get(Band, scope.band_id)
    if band is None or (not band.member_roster_public and not scope.is_member):
        raise NotFoundError('Resource not found')
    return band


def _member_or_404(member_id, scope):
    _roster_visible_or_404(scope)
    member = Member.query.filter(Member.id == member_id, Member.band_id == scope.band_id).one_or_none()
    if member is None:
        raise NotFoundError('Resource not found')
    return member


def _personal_logs_query(scope):
    member_is_scoped = exists().where(and_(Member.id == PersonalLog.member_id,
                                           Member.band_id == scope.band_id))
    return visible_query(PersonalLog.query.options(selectinload(PersonalLog.member)), PersonalLog, scope,
                         parent_visibility=scope.band_visibility).filter(
                             member_is_scoped, _creator_is_scoped_sql(PersonalLog, scope),
                         )


def _personal_log_or_404(log_id, scope):
    log = _personal_logs_query(scope).filter(PersonalLog.id == log_id).one_or_none()
    if log is None:
        raise NotFoundError('Resource not found')
    return log


def _personal_comment_graph_sql(scope, log_id):
    parent = aliased(Comment)
    parent_is_scoped = exists().where(and_(parent.id == Comment.parent_id,
                                           parent.band_id == scope.band_id,
                                           parent.personal_log_id == log_id))
    return (
        Comment.band_id == scope.band_id,
        Comment.personal_log_id == log_id,
        _creator_is_scoped_sql(Comment, scope),
        or_(Comment.parent_id.is_(None), parent_is_scoped),
    )


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


def _media_graph_sql(scope):
    song_is_scoped = exists().where(and_(Song.id == Media.song_id, Song.band_id == scope.band_id))
    rehearsal_is_scoped = or_(
        Media.rehearsal_id.is_(None),
        exists().where(and_(Rehearsal.id == Media.rehearsal_id, Rehearsal.band_id == scope.band_id)),
    )
    foreign_song_rehearsal = exists().where(and_(
        rehearsal_songs.c.song_id == Media.song_id,
        rehearsal_songs.c.rehearsal_id == Rehearsal.id,
        Rehearsal.band_id != scope.band_id,
    ))
    predicates = [song_is_scoped, rehearsal_is_scoped, ~foreign_song_rehearsal]
    if not scope.is_member:
        song_is_public = exists().where(and_(
            Song.id == Media.song_id, Song.band_id == scope.band_id, Song.visibility == 'PUBLIC',
        ))
        rehearsal_is_public = or_(
            Media.rehearsal_id.is_(None),
            exists().where(and_(
                Rehearsal.id == Media.rehearsal_id, Rehearsal.band_id == scope.band_id,
                Rehearsal.visibility == 'PUBLIC',
            )),
        )
        no_member_song_rehearsal = ~exists().where(and_(
            rehearsal_songs.c.song_id == Media.song_id,
            rehearsal_songs.c.rehearsal_id == Rehearsal.id,
            Rehearsal.band_id == scope.band_id,
            Rehearsal.visibility != 'PUBLIC',
        ))
        predicates.extend((song_is_public, rehearsal_is_public, no_member_song_rehearsal))
    return predicates


def _rehearsal_graph_sql(scope):
    linked_rehearsal = aliased(Rehearsal)
    foreign_song = exists().where(and_(
        rehearsal_songs.c.rehearsal_id == Rehearsal.id,
        rehearsal_songs.c.song_id == Song.id,
        Song.band_id != scope.band_id,
    ))
    foreign_media = exists().where(and_(Media.rehearsal_id == Rehearsal.id, Media.band_id != scope.band_id))
    foreign_media_song = exists().where(and_(
        Media.rehearsal_id == Rehearsal.id, Media.song_id == Song.id, Song.band_id != scope.band_id,
    ))
    foreign_media_song_rehearsal = exists().where(and_(
        Media.rehearsal_id == Rehearsal.id,
        rehearsal_songs.c.song_id == Media.song_id,
        rehearsal_songs.c.rehearsal_id == linked_rehearsal.id,
        linked_rehearsal.band_id != scope.band_id,
    ))
    return ~foreign_song, ~foreign_media, ~foreign_media_song, ~foreign_media_song_rehearsal


def _song_or_404(song_id, scope):
    song = visible_query(Song.query, Song, scope, parent_visibility=scope.band_visibility).filter(
        Song.id == song_id, *_song_graph_sql(scope),
    ).one_or_none()
    if song is None or not visible_to_scope(scope, song_visibility(scope, song)):
        raise NotFoundError('Resource not found')
    return song


def _rehearsal_or_404(rehearsal_id, scope):
    rehearsal = visible_query(Rehearsal.query, Rehearsal, scope, parent_visibility=scope.band_visibility).filter(
        Rehearsal.id == rehearsal_id, *_rehearsal_graph_sql(scope),
    ).one_or_none()
    if rehearsal is None or not visible_to_scope(scope, rehearsal_visibility(scope, rehearsal)):
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
            selectinload(Song.rehearsals),
        ), Song, scope, parent_visibility=scope.band_visibility,
    ).filter(*_song_graph_sql(scope))
    page, pagination = _page_query(query, (Song.id.asc(),), limit, offset)
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
    ).filter(Media.song_id == song.id, *_media_graph_sql(scope))
    page, pagination = _page_query(query, (Media.vote_score.desc(), Media.id.asc()), limit, offset)
    return jsonify({'media': [serialize_media(scope, item) for item in page], 'pagination': pagination})


@content_reads_bp.get('/rehearsals')
@optional_auth
def list_rehearsals(band_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    query = visible_query(
        Rehearsal.query.options(
            selectinload(Rehearsal.songs),
        ), Rehearsal, scope, parent_visibility=scope.band_visibility,
    ).filter(*_rehearsal_graph_sql(scope))
    page, pagination = _page_query(query, (Rehearsal.date.asc(), Rehearsal.id.asc()), limit, offset)
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
    ).filter(Media.rehearsal_id == rehearsal.id, *_media_graph_sql(scope))
    page, pagination = _page_query(query, (Media.vote_score.desc(), Media.id.asc()), limit, offset)
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
    # v2 intentionally exposes a flat, bounded comment feed. ``parent_id``
    # lets a client reconstruct threads without recursively fetching an
    # unbounded descendant tree.
    comments = Comment.query.filter(
        Comment.band_id == scope.band_id,
        Comment.media_id == media.id,
    )
    page, pagination = _page_query(comments, (Comment.created_at.asc(), Comment.id.asc()), limit, offset)
    return jsonify({'comments': [serialize_comment(scope, item, visibility, include_replies=False) for item in page],
                    'pagination': pagination})


# Remaining Phase 6 read domains live on this blueprint so all v2 reads share
# the exact optional-auth/cache/schema boundary above.  The legacy blueprints
# remain intentionally untouched for the import-band compatibility API.
@content_reads_bp.get('/dashboard/stats')
@optional_auth
def get_dashboard_stats(band_id):
    scope = _scope(band_id)
    songs = visible_query(Song.query, Song, scope, parent_visibility=scope.band_visibility).filter(
        *_song_graph_sql(scope), _creator_is_scoped_sql(Song, scope),
    )
    total_songs = songs.with_entities(func.count(Song.id)).scalar() or 0
    rows = songs.with_entities(Song.status, func.count(Song.id)).group_by(Song.status).all()
    return jsonify({'stats': {
        'total_songs': total_songs,
        'status_counts': {status: count for status, count in rows},
    }})


@content_reads_bp.get('/members')
@optional_auth
def list_members(band_id):
    scope = _scope(band_id)
    _roster_visible_or_404(scope)
    limit, offset = _page_request()
    query = Member.query.filter(Member.band_id == scope.band_id)
    page, pagination = _page_query(query, (Member.name.asc(), Member.id.asc()), limit, offset)
    return jsonify({'members': [serialize_member(scope, item) for item in page], 'pagination': pagination})


@content_reads_bp.get('/members/<int:member_id>')
@optional_auth
def get_member(band_id, member_id):
    scope = _scope(band_id)
    return jsonify({'member': serialize_member(scope, _member_or_404(member_id, scope))})


@content_reads_bp.get('/members/<int:member_id>/logs')
@optional_auth
def list_member_logs(band_id, member_id):
    scope = _scope(band_id)
    member = _member_or_404(member_id, scope)
    limit, offset = _page_request()
    query = _personal_logs_query(scope).filter(PersonalLog.member_id == member.id)
    page, pagination = _page_query(query, (PersonalLog.created_at.desc(), PersonalLog.id.desc()), limit, offset)
    return jsonify({'logs': [serialize_personal_log(scope, item) for item in page], 'pagination': pagination})


@content_reads_bp.get('/personal-logs/<int:log_id>/processing')
@optional_auth
def get_personal_log_processing(band_id, log_id):
    scope = _scope(band_id)
    return jsonify({'processing': serialize_personal_processing(scope, _personal_log_or_404(log_id, scope))})


@content_reads_bp.get('/personal-logs/<int:log_id>/comments')
@optional_auth
def get_personal_log_comments(band_id, log_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    log = _personal_log_or_404(log_id, scope)
    page, pagination = _page_query(
        Comment.query.filter(*_personal_comment_graph_sql(scope, log.id)),
        (Comment.created_at.asc(), Comment.id.asc()), limit, offset,
    )
    visibility = personal_log_visibility(scope, log)
    return jsonify({'comments': [serialize_personal_comment(scope, item, visibility) for item in page],
                    'pagination': pagination})


def _gallery_query(scope):
    return visible_query(GalleryImage.query, GalleryImage, scope,
                         parent_visibility=scope.band_visibility).filter(
                             _creator_is_scoped_sql(GalleryImage, scope),
                         )


@content_reads_bp.get('/gallery')
@optional_auth
def list_gallery(band_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    page, pagination = _page_query(_gallery_query(scope),
                                   (GalleryImage.created_at.desc(), GalleryImage.id.desc()), limit, offset)
    return jsonify({'gallery': [serialize_gallery_image(scope, item) for item in page], 'pagination': pagination})


@content_reads_bp.get('/gallery/featured')
@optional_auth
def get_featured_gallery(band_id):
    scope = _scope(band_id)
    # v06 creates a per-band partial uniqueness constraint.  Do not choose an
    # arbitrary row if a manually corrupted database contains multiple rows.
    featured = _gallery_query(scope).filter(GalleryImage.is_featured.is_(True)).order_by(
        GalleryImage.id.asc()).limit(2).all()
    if len(featured) != 1:
        raise NotFoundError('Resource not found')
    return jsonify({'gallery': serialize_gallery_image(scope, featured[0])})


def _announcement_query(scope):
    return visible_query(Announcement.query, Announcement, scope,
                         parent_visibility=scope.band_visibility).filter(
                             _creator_is_scoped_sql(Announcement, scope),
                         )


@content_reads_bp.get('/announcement')
@optional_auth
def get_band_announcement(band_id):
    scope = _scope(band_id)
    values = _announcement_query(scope).order_by(Announcement.id.asc()).limit(2).all()
    if len(values) != 1:
        raise NotFoundError('Resource not found')
    return jsonify({'announcement': serialize_announcement(scope, values[0])})


@content_reads_bp.get('/suggestions')
@optional_auth
def list_suggestions(band_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    query = visible_query(SongSuggestion.query, SongSuggestion, scope,
                          parent_visibility=scope.band_visibility).filter(
                              _creator_is_scoped_sql(SongSuggestion, scope),
                          )
    score = (func.coalesce(SongSuggestion.thumbs_up, 0) - func.coalesce(SongSuggestion.thumbs_down, 0))
    page, pagination = _page_query(query, (score.desc(), SongSuggestion.id.asc()), limit, offset)
    return jsonify({'suggestions': [serialize_suggestion(scope, item) for item in page],
                    'pagination': pagination})
