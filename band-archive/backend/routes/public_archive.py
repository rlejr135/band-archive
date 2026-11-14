"""Anonymous public archive with explicit metadata-only projections."""

from flask import Blueprint, after_this_request, current_app, jsonify, request

from band_access import PUBLIC, BandReadScope, RequestAudience
from content_queries import _featured_gallery_rows_query, _gallery_query
from errors import NotFoundError, ValidationError
from media_access_service import media_playback
from models import Band, GalleryImage, Media, Song
from public_archive_service import public_media_query, public_song_query
from routes.bands import _logo_for_scope
from v2_remaining_read_serializers import serialize_gallery_image


public_archive_bp = Blueprint('public_archive', __name__, url_prefix='/api/v2/public/archive')


@public_archive_bp.before_request
def _prepare_request():
    @after_this_request
    def no_store(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    if not current_app.config.get('BAND_API_SCHEMA_READY', False):
        return jsonify({'error': 'band_schema_unavailable'}), 503


def _page(query, *order):
    try:
        limit = int(request.args.get('limit', 20))
        offset = int(request.args.get('offset', 0))
    except (ValueError, TypeError):
        raise ValidationError('limit and offset must be integers.')
    if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
        raise ValidationError('limit or offset is outside the supported range.')
    items = query.order_by(*order).offset(offset).limit(limit + 1).all()
    return items[:limit], {'has_more': len(items) > limit}


def _band_ref(band):
    return {'name': band.name, 'slug': band.slug}


def _band_detail(band):
    result = dict(_band_ref(band), description=None, logo_url=None, featured_image_url=None)
    if band.default_visibility != PUBLIC:
        return result
    scope = BandReadScope(band.id, band.slug, RequestAudience(None, None), None, PUBLIC)
    result['description'] = band.description
    logo = _logo_for_scope(scope)
    if logo:
        result['logo_url'] = logo['url']
    selected = _featured_gallery_rows_query(scope).all()
    if len(selected) == 1:
        image = _gallery_query(scope).filter(GalleryImage.id == selected[0].id).one_or_none()
        if image is not None:
            try:
                result['featured_image_url'] = serialize_gallery_image(scope, image)['url']
            except NotFoundError:
                pass
    return result


def _song_ref(song, band=None):
    result = {'id': song.id, 'title': song.title, 'artist': song.artist}
    if band is not None:
        result['band'] = _band_ref(band)
    return result


def _media_ref(item, song, band):
    return {
        'id': item.id, 'file_type': item.file_type,
        'display_name': '영상' if item.file_type == 'video' else '음원',
        'created_at': item.created_at.isoformat() if item.created_at else None,
        'song': _song_ref(song), 'band': _band_ref(band),
    }


def _one_or_404(query):
    item = query.one_or_none()
    if item is None:
        raise NotFoundError('Resource not found')
    return item


def _media_rows():
    eligible_ids = public_media_query().with_entities(Media.id).statement
    return Media.query.filter(Media.id.in_(eligible_ids)).join(
        Song, (Song.id == Media.song_id) & (Song.band_id == Media.band_id),
    ).join(Band, Band.id == Media.band_id).add_entity(Song).add_entity(Band)


def _song_rows():
    return public_song_query().add_entity(Band)


@public_archive_bp.get('/media')
def media_feed():
    rows, pagination = _page(_media_rows(), Media.created_at.desc(), Media.id.desc())
    return jsonify(media=[_media_ref(*row) for row in rows], pagination=pagination)


@public_archive_bp.get('/songs')
def song_feed():
    rows, pagination = _page(_song_rows(), Song.created_at.desc(), Song.id.desc())
    return jsonify(songs=[_song_ref(*row) for row in rows], pagination=pagination)


@public_archive_bp.get('/bands/<string:slug>')
def band_detail(slug):
    band = _one_or_404(Band.query.filter_by(slug=slug, deleted_at=None))
    query = public_song_query().filter(Song.band_id == band.id)
    if band.default_visibility != PUBLIC and not query.with_entities(Song.id).first():
        raise NotFoundError('Resource not found')
    rows, pagination = _page(query, Song.created_at.desc(), Song.id.desc())
    return jsonify(band=_band_detail(band), songs=[_song_ref(song, band) for song in rows], pagination=pagination)


@public_archive_bp.get('/bands/<string:slug>/songs/<int:song_id>')
def song_detail(slug, song_id):
    song, band = _one_or_404(_song_rows().filter(Band.slug == slug, Song.id == song_id))
    rows, pagination = _page(
        _media_rows().filter(Media.band_id == band.id, Media.song_id == song.id),
        Media.created_at.desc(), Media.id.desc(),
    )
    return jsonify(band=_band_detail(band), song=_song_ref(song, band),
                   media=[_media_ref(*row) for row in rows], pagination=pagination)


@public_archive_bp.get('/media/<int:media_id>')
def media_detail(media_id):
    item, song, band = _one_or_404(_media_rows().filter(Media.id == media_id))
    result = _media_ref(item, song, band)
    result['playback'] = media_playback(item, PUBLIC)
    return jsonify(band=_band_ref(band), song=_song_ref(song, band), media=result)
