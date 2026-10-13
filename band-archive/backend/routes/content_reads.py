"""Tenant-scoped, optional-authenticated v2 reads for songs and rehearsals."""

from datetime import date, datetime, timedelta, timezone
import re

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import aliased, selectinload

from band_access import (
    content_capabilities, current_audience, optional_auth, read_scope_for_id, visible_object_or_404,
    visible_query,
)
from errors import NotFoundError, ValidationError
from extensions import db
from models import (Announcement, Band, BandMembership, Comment, GalleryImage, Media, MediaVote,
                    Member, PersonalLog, Rehearsal, Song, SongSuggestion, SongSuggestionVote, rehearsal_songs)
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
KOREA_TZ = timezone(timedelta(hours=9))
SONG_STATUSES = ('Practice', 'Completed', 'OnHold')


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


def _song_query(scope, *, with_rehearsals=True):
    """Return the common visible song graph used by lists and summaries."""
    query = Song.query.options(selectinload(Song.rehearsals)) if with_rehearsals else Song.query
    return visible_query(
        query, Song, scope,
        parent_visibility=scope.band_visibility,
    ).filter(*_song_graph_sql(scope), _creator_is_scoped_sql(Song, scope))


def _rehearsal_query(scope, *, with_songs=True):
    """Return the common visible rehearsal graph used by lists and summaries."""
    query = Rehearsal.query.options(selectinload(Rehearsal.songs)) if with_songs else Rehearsal.query
    return visible_query(
        query, Rehearsal, scope,
        parent_visibility=scope.band_visibility,
    ).filter(*_rehearsal_graph_sql(scope), _creator_is_scoped_sql(Rehearsal, scope))


def _dashboard_rehearsal_summary_map(scope, rehearsals):
    """Serialize bounded visible song refs for a bounded set of rehearsals."""
    if not rehearsals:
        return {}
    rehearsal_ids = [item.id for item in rehearsals]
    visible_song_ids = _song_query(scope, with_rehearsals=False).order_by(None).with_entities(Song.id).subquery()
    ranked = db.session.query(
        rehearsal_songs.c.rehearsal_id.label('rehearsal_id'),
        Song.id.label('song_id'),
        func.row_number().over(
            partition_by=rehearsal_songs.c.rehearsal_id,
            order_by=Song.id.asc(),
        ).label('position'),
    ).join(Song, Song.id == rehearsal_songs.c.song_id).filter(
        rehearsal_songs.c.rehearsal_id.in_(rehearsal_ids),
        Song.id.in_(select(visible_song_ids.c.id)),
    ).subquery()
    rows = db.session.query(ranked.c.rehearsal_id, Song).join(
        Song, Song.id == ranked.c.song_id,
    ).filter(ranked.c.position <= 6).order_by(
        ranked.c.rehearsal_id.asc(), ranked.c.position.asc(),
    ).all()
    songs_by_rehearsal = {rehearsal_id: [] for rehearsal_id in rehearsal_ids}
    for rehearsal_id, song in rows:
        songs_by_rehearsal[rehearsal_id].append(song)

    result = {}
    for rehearsal in rehearsals:
        songs = songs_by_rehearsal[rehearsal.id]
        visibility = rehearsal_visibility(scope, rehearsal)
        value = {
            'id': rehearsal.id,
            'title': (rehearsal.title or '')[:200],
            'date': rehearsal.date.isoformat() if rehearsal.date else None,
            'start_date': rehearsal.start_date.isoformat() if rehearsal.start_date else None,
            'end_date': rehearsal.end_date.isoformat() if rehearsal.end_date else None,
            'time': (rehearsal.time or '')[:20] or None,
            'location': (rehearsal.location or '')[:200] or None,
            'color': (rehearsal.color or '')[:7] or None,
            'visibility': rehearsal.visibility,
            'effective_visibility': visibility,
            **content_capabilities(scope, rehearsal),
        }
        value['songs'] = [
            {
                'id': song.id,
                'title': (song.title or '')[:100],
                'artist': (song.artist or '')[:100],
                'status': (song.status or '')[:20] or None,
                'visibility': song.visibility,
                'effective_visibility': song_visibility(scope, song),
                **content_capabilities(scope, song),
            }
            for song in songs[:5]
        ]
        value['has_more_songs'] = len(songs) > 5
        result[rehearsal.id] = value
    return result


def _dashboard_song_dict(scope, song):
    visibility = song_visibility(scope, song)
    return {
        'id': song.id,
        'title': (song.title or '')[:100],
        'artist': (song.artist or '')[:100],
        'status': (song.status or '')[:20] or None,
        'visibility': song.visibility,
        'effective_visibility': visibility,
        **content_capabilities(scope, song),
    }


def _dashboard_month():
    raw = request.args.get('month')
    if raw is None:
        raw = datetime.now(timezone.utc).astimezone(KOREA_TZ).strftime('%Y-%m')
    if not isinstance(raw, str) or re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', raw) is None:
        raise ValidationError('month must use YYYY-MM with a valid month.')
    year, month = (int(part) for part in raw.split('-'))
    try:
        start = date(year, month, 1)
        end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    except ValueError:
        raise ValidationError('month is outside the supported date range.')
    return raw, start, end


def _page_viewer_votes(scope, media_page):
    """Fetch this active member's v2 votes for one media page in one query."""
    if scope.membership is None or not media_page:
        return {}
    ids = [item.id for item in media_page]
    rows = MediaVote.query.filter(
        MediaVote.band_id == scope.band_id,
        MediaVote.created_by_membership_id == scope.membership.id,
        MediaVote.is_legacy.is_(False),
        MediaVote.media_id.in_(ids),
    ).all()
    return {row.media_id: row.value for row in rows if row.value in (-1, 1)}


def _page_suggestion_viewer_votes(scope, suggestion_page):
    """Load page vote state in one tenant- and membership-bound query."""
    if scope.audience.user_id is None or scope.membership is None or not suggestion_page:
        return {}
    ids = [item.id for item in suggestion_page]
    rows = SongSuggestionVote.query.filter(
        SongSuggestionVote.band_id == scope.band_id,
        SongSuggestionVote.voter_user_id == scope.audience.user_id,
        SongSuggestionVote.is_legacy.is_(False),
        SongSuggestionVote.suggestion_id.in_(ids),
    ).all()
    return {row.suggestion_id: row.value for row in rows if row.value in (-1, 1)}


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
        Song.query.options(selectinload(Song.rehearsals)), Song, scope,
        parent_visibility=scope.band_visibility,
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
    viewer_votes = _page_viewer_votes(scope, page)
    return jsonify({'media': [serialize_media(scope, item, viewer_votes.get(item.id, 0)) for item in page], 'pagination': pagination})


@content_reads_bp.get('/rehearsals')
@optional_auth
def list_rehearsals(band_id):
    scope = _scope(band_id)
    limit, offset = _page_request()
    query = visible_query(
        Rehearsal.query.options(selectinload(Rehearsal.songs)), Rehearsal, scope,
        parent_visibility=scope.band_visibility,
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
    viewer_votes = _page_viewer_votes(scope, page)
    return jsonify({'media': [serialize_media(scope, item, viewer_votes.get(item.id, 0)) for item in page], 'pagination': pagination})


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
    songs = _song_query(scope, with_rehearsals=False)
    total_songs = songs.with_entities(func.count(Song.id)).scalar() or 0
    rows = songs.with_entities(Song.status, func.count(Song.id)).group_by(Song.status).all()
    return jsonify({'stats': {
        'total_songs': total_songs,
        'status_counts': {status: count for status, count in rows},
    }})


@content_reads_bp.get('/dashboard')
@optional_auth
def get_band_dashboard(band_id):
    """Return a bounded, audience-scoped snapshot for one band workspace."""
    scope = _scope(band_id)
    month, month_start, month_end = _dashboard_month()
    limit, offset = _page_request()
    today = datetime.now(timezone.utc).astimezone(KOREA_TZ).date()

    songs = _song_query(scope, with_rehearsals=False)
    total_songs = songs.order_by(None).with_entities(func.count(Song.id)).scalar() or 0
    status_rows = songs.order_by(None).with_entities(Song.status, func.count(Song.id)).group_by(Song.status).all()
    status_counts = {status: 0 for status in SONG_STATUSES}
    status_counts['Other'] = 0
    for status, count in status_rows:
        key = status if status in SONG_STATUSES else 'Other'
        status_counts[key] += count

    rehearsals = _rehearsal_query(scope, with_songs=False)
    total_rehearsals = rehearsals.order_by(None).with_entities(func.count(Rehearsal.id)).scalar() or 0

    practice_songs = songs.filter(Song.status == 'Practice').order_by(Song.id.asc()).limit(5).all()

    # Keep the legacy date interpretation: ``date`` is the canonical single
    # day; a range participates only when both bounds exist. Range end is
    # inclusive, and the next month's first day is excluded.
    month_rehearsals_query = rehearsals.filter(or_(
        and_(Rehearsal.date >= month_start, Rehearsal.date < month_end),
        and_(Rehearsal.start_date.isnot(None), Rehearsal.end_date.isnot(None),
             Rehearsal.start_date <= Rehearsal.end_date,
             Rehearsal.start_date < month_end, Rehearsal.end_date >= month_start),
    ))
    month_rows, month_pagination = _page_query(
        month_rehearsals_query, (Rehearsal.date.asc(), Rehearsal.id.asc()), limit, offset,
    )

    valid_range = and_(Rehearsal.start_date.isnot(None), Rehearsal.end_date.isnot(None),
                       Rehearsal.start_date <= Rehearsal.end_date)
    upcoming_rows = rehearsals.filter(or_(
        and_(valid_range, Rehearsal.end_date >= today),
        and_(~valid_range, Rehearsal.date >= today),
    )).order_by(
        # A valid range is presented by its start; otherwise its canonical date.
        case((valid_range, Rehearsal.start_date), else_=Rehearsal.date).asc(),
        Rehearsal.date.asc(), Rehearsal.id.asc(),
    ).limit(3).all()

    rehearsal_summary_rows = list({
        item.id: item for item in (*month_rows, *upcoming_rows)
    }.values())
    rehearsal_summaries = _dashboard_rehearsal_summary_map(scope, rehearsal_summary_rows)

    featured_gallery = None
    featured_rows = _featured_gallery_rows_query(scope).all()
    if len(featured_rows) == 1:
        featured_image = _gallery_query(scope).filter(GalleryImage.id == featured_rows[0].id).one_or_none()
        if featured_image is not None:
            featured_gallery = serialize_gallery_image(scope, featured_image)

    recent_images = _gallery_query(scope).order_by(
        GalleryImage.created_at.desc(), GalleryImage.id.desc(),
    ).limit(4).all()
    announcement_rows = _announcement_query(scope).order_by(Announcement.id.asc()).limit(2).all()
    announcement = serialize_announcement(scope, announcement_rows[0]) if len(announcement_rows) == 1 else None
    if announcement is not None:
        announcement_content = announcement.get('content') or ''
        announcement['content'] = announcement_content[:10_000]
        if len(announcement_content) > 10_000:
            announcement['content_truncated'] = True

    return jsonify({'dashboard': {
        'band_id': scope.band_id,
        'month': month,
        'today': today.isoformat(),
        'stats': {
            'total_songs': total_songs,
            'total_rehearsals': total_rehearsals,
            'status_counts': status_counts,
        },
        'practice_songs': [_dashboard_song_dict(scope, song) for song in practice_songs],
        'month_rehearsals': {
            'items': [rehearsal_summaries[item.id] for item in month_rows],
            'pagination': month_pagination,
        },
        'upcoming_rehearsals': [rehearsal_summaries[item.id] for item in upcoming_rows],
        'featured_gallery': featured_gallery,
        'recent_gallery': [serialize_gallery_image(scope, image) for image in recent_images],
        'announcement': announcement,
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
    ).order_by(GalleryImage.id.asc()).limit(2)


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
    # v06 creates a per-band partial uniqueness constraint.  First inspect
    # every same-band featured row, independent of this request's visibility,
    # and never choose an arbitrary representation from a corrupt duplicate.
    featured = _featured_gallery_rows_query(scope).all()
    if len(featured) != 1:
        raise NotFoundError('Resource not found')
    # Only after the invariant has passed may the audience-filtered query
    # decide whether the one row is discoverable or may trigger URL signing.
    image = _gallery_query(scope).filter(GalleryImage.id == featured[0].id).one_or_none()
    if image is None:
        raise NotFoundError('Resource not found')
    return jsonify({'gallery': serialize_gallery_image(scope, image)})


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
    viewer_votes = _page_suggestion_viewer_votes(scope, page)
    return jsonify({'suggestions': [serialize_suggestion(scope, item, viewer_votes.get(item.id, 0)) for item in page],
                    'pagination': pagination})
