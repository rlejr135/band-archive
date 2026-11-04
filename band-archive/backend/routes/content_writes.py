"""Authenticated, tenant-bound v2 mutations for core band content.

The v1 blueprints intentionally retain their device/password-based behaviour.
This module never delegates to them: an authenticated membership is the only
authority for these writes and all object lookups retain the URL band id.
"""

from functools import wraps

from flask import Blueprint, after_this_request, current_app, jsonify, request

from auth_service import current_app_user
from announcement_service import (
    ANNOUNCEMENT_FIELDS as _ANNOUNCEMENT_FIELDS,
    clear_announcement, put_announcement,
)
from band_logo_service import set_band_logo as update_band_logo_selection
from comment_service import create_comment, delete_comment, update_comment
from content_visibility import (
    media_visibility, personal_log_visibility,
)
from errors import ValidationError
from featured_media_service import clear_song_featured_media, set_song_featured_media
from gallery_featured_service import set_gallery_featured
from models import Media
from rehearsal_service import (
    REHEARSAL_FIELDS as _REHEARSAL_FIELDS,
    create_rehearsal as create_band_rehearsal, delete_rehearsal as delete_band_rehearsal,
    update_rehearsal as update_band_rehearsal,
)
from song_suggestion_service import (
    SUGGESTION_FIELDS as _SUGGESTION_FIELDS,
    create_song_suggestion, delete_song_suggestion, proceed_song_suggestion,
    update_song_suggestion,
)
from song_service import (
    SONG_FIELDS as _SONG_FIELDS,
    create_song as create_band_song, delete_song as delete_band_song,
    update_song as update_band_song,
)
from v2_vote_service import set_media_vote, set_song_suggestion_vote
from v2_content_serializers import (
    serialize_comment, serialize_media,
    serialize_rehearsal, serialize_song,
)
from v2_remaining_read_serializers import serialize_personal_comment
from v2_remaining_read_serializers import serialize_announcement, serialize_gallery_image, serialize_suggestion


content_writes_bp = Blueprint('content_writes', __name__, url_prefix='/api/v2/bands/<int:band_id>')

_IGNORED_OWNERSHIP_FIELDS = {'band_id', 'created_by_membership_id', 'created_by_user_id', 'is_legacy'}


def _no_store():
    @after_this_request
    def add_headers(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Authorization')
        return response


def authenticated_write(view):
    """Authenticate once and mark success as well as error responses private."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        _no_store()
        current_app_user(required=True)
        return view(*args, **kwargs)
    return wrapped


@content_writes_bp.put('/logo')
@authenticated_write
def set_band_logo(band_id):
    data = _payload()
    if set(data) != {'gallery_image_id'}:
        raise ValidationError('Only gallery_image_id is accepted.')
    image_id = data['gallery_image_id']
    if image_id is not None and (type(image_id) is not int or image_id <= 0):
        raise ValidationError('gallery_image_id must be a positive integer or null.')
    user = current_app_user(required=True)

    def prepare_logo(scope, image):
        return serialize_gallery_image(scope, image)

    return jsonify(update_band_logo_selection(
        band_id=band_id, actor=user, image_id=image_id, prepare_logo=prepare_logo,
    ))


@content_writes_bp.before_request
def require_content_write_schema():
    if not current_app.config.get('BAND_API_SCHEMA_READY', False):
        return jsonify({
            'error': 'band_schema_unavailable',
            'message': 'Band API requires the isolated Alembic migration before startup.',
        }), 503


def _payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValidationError('A JSON object is required.')
    return value





def _check_fields(data, allowed):
    unknown = set(data) - allowed - _IGNORED_OWNERSHIP_FIELDS
    if unknown:
        raise ValidationError('Unsupported field: ' + sorted(unknown)[0] + '.')


def _comment_response(scope, comment, target):
    if isinstance(target, Media):
        return serialize_comment(scope, comment, media_visibility(scope, target), include_replies=False)
    return serialize_personal_comment(scope, comment, personal_log_visibility(scope, target))


@content_writes_bp.post('/songs')
@authenticated_write
def create_song(band_id):
    data = _payload()
    _check_fields(data, _SONG_FIELDS)
    def prepare_response(scope, song):
        return jsonify({'song': serialize_song(scope, song)}), 201
    return create_band_song(band_id=band_id, data=data, prepare_response=prepare_response)


@content_writes_bp.patch('/songs/<int:song_id>')
@authenticated_write
def update_song(band_id, song_id):
    data = _payload(); _check_fields(data, _SONG_FIELDS)
    def prepare_response(scope, song):
        return jsonify({'song': serialize_song(scope, song)})
    return update_band_song(
        band_id=band_id, song_id=song_id, data=data, prepare_response=prepare_response,
    )


@content_writes_bp.delete('/songs/<int:song_id>')
@authenticated_write
def delete_song(band_id, song_id):
    def prepare_response(deleted_id):
        return jsonify({'deleted': True, 'id': deleted_id})
    return delete_band_song(
        band_id=band_id, song_id=song_id, prepare_response=prepare_response,
    )


@content_writes_bp.post('/rehearsals')
@authenticated_write
def create_rehearsal(band_id):
    data = _payload(); _check_fields(data, _REHEARSAL_FIELDS)
    def prepare_response(scope, rehearsal):
        return jsonify({'rehearsal': serialize_rehearsal(scope, rehearsal)}), 201
    return create_band_rehearsal(band_id=band_id, data=data, prepare_response=prepare_response)


@content_writes_bp.patch('/rehearsals/<int:rehearsal_id>')
@authenticated_write
def update_rehearsal(band_id, rehearsal_id):
    data = _payload(); _check_fields(data, _REHEARSAL_FIELDS)
    def prepare_response(scope, rehearsal):
        return jsonify({'rehearsal': serialize_rehearsal(scope, rehearsal)})
    return update_band_rehearsal(
        band_id=band_id, rehearsal_id=rehearsal_id, data=data, prepare_response=prepare_response,
    )


@content_writes_bp.delete('/rehearsals/<int:rehearsal_id>')
@authenticated_write
def delete_rehearsal(band_id, rehearsal_id):
    def prepare_response(deleted_id):
        return jsonify({'deleted': True, 'id': deleted_id})
    return delete_band_rehearsal(
        band_id=band_id, rehearsal_id=rehearsal_id, prepare_response=prepare_response,
    )


def _create_comment(band_id, kind, target_id):
    data = _payload()
    # Ownership-looking fields are intentionally ignored.  author/password
    # are not accepted by the v2 contract and cannot impersonate an account.
    allowed = {'content', 'parent_id', 'timestamp_seconds'} | _IGNORED_OWNERSHIP_FIELDS | {'author', 'password'}
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError('Unsupported field: ' + sorted(unknown)[0] + '.')
    def prepare_response(scope, comment, target):
        return jsonify({'comment': _comment_response(scope, comment, target)}), 201
    return create_comment(
        band_id=band_id, kind=kind, target_id=target_id, data=data, prepare_response=prepare_response,
    )


@content_writes_bp.post('/media/<int:media_id>/comments')
@authenticated_write
def create_media_comment_v2(band_id, media_id):
    return _create_comment(band_id, 'media', media_id)


@content_writes_bp.post('/personal-logs/<int:log_id>/comments')
@authenticated_write
def create_personal_log_comment_v2(band_id, log_id):
    return _create_comment(band_id, 'personal_log', log_id)


@content_writes_bp.patch('/comments/<int:comment_id>')
@authenticated_write
def update_comment_v2(band_id, comment_id):
    data = _payload()
    allowed = {'content', 'timestamp_seconds'} | _IGNORED_OWNERSHIP_FIELDS | {'author', 'password', 'parent_id'}
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError('Unsupported field: ' + sorted(unknown)[0] + '.')
    def prepare_response(scope, comment, target):
        return jsonify({'comment': _comment_response(scope, comment, target)})
    return update_comment(
        band_id=band_id, comment_id=comment_id, data=data, prepare_response=prepare_response,
    )


@content_writes_bp.delete('/comments/<int:comment_id>')
@authenticated_write
def delete_comment_v2(band_id, comment_id):
    def prepare_response(deleted_id):
        return jsonify({'deleted': True, 'id': deleted_id})
    return delete_comment(band_id=band_id, comment_id=comment_id, prepare_response=prepare_response)



def _vote_value(data):
    value = data.get('value')
    if isinstance(value, bool) or not isinstance(value, int) or value not in {-1, 0, 1}:
        raise ValidationError('value must be -1, 0, or 1.')
    return value


def _apply_media_vote(band_id, media_id):
    data = _payload(); value = _vote_value(data)
    if set(data) - {'value'}:
        raise ValidationError('Unsupported vote fields.')
    def prepare_response(scope, media, viewer_vote):
        return jsonify({'media': serialize_media(scope, media, viewer_vote), 'viewer_vote': viewer_vote})
    return set_media_vote(band_id=band_id, media_id=media_id, value=value, prepare_response=prepare_response)


@content_writes_bp.put('/media/<int:media_id>/vote')
@authenticated_write
def put_media_vote_v2(band_id, media_id):
    return _apply_media_vote(band_id, media_id)


@content_writes_bp.patch('/media/<int:media_id>/vote')
@authenticated_write
def patch_media_vote_v2(band_id, media_id):
    return _apply_media_vote(band_id, media_id)


# Announcement and recommendation queue writes live here rather than in their
# v1 blueprints.  The legacy endpoints deliberately retain their historical
# password/device contract for old clients; these routes have no password
# input and derive every tenant/actor field from the authenticated membership.
def _suggestion_response(scope, suggestion, viewer_vote=0):
    return serialize_suggestion(scope, suggestion, viewer_vote)



@content_writes_bp.post('/suggestions')
@authenticated_write
def create_suggestion_v2(band_id):
    data = _payload(); _check_fields(data, _SUGGESTION_FIELDS)
    def prepare_response(scope, suggestion):
        return jsonify({'suggestion': _suggestion_response(scope, suggestion)}), 201
    return create_song_suggestion(band_id=band_id, data=data, prepare_response=prepare_response)


@content_writes_bp.patch('/suggestions/<int:suggestion_id>')
@authenticated_write
def update_suggestion_v2(band_id, suggestion_id):
    data = _payload(); _check_fields(data, _SUGGESTION_FIELDS)
    def prepare_response(scope, suggestion):
        return jsonify({'suggestion': _suggestion_response(scope, suggestion)})
    return update_song_suggestion(
        band_id=band_id, suggestion_id=suggestion_id, data=data, prepare_response=prepare_response,
    )


@content_writes_bp.delete('/suggestions/<int:suggestion_id>')
@authenticated_write
def delete_suggestion_v2(band_id, suggestion_id):
    def prepare_response(suggestion_id):
        return jsonify({'deleted': True, 'id': suggestion_id})
    return delete_song_suggestion(
        band_id=band_id, suggestion_id=suggestion_id, prepare_response=prepare_response,
    )


def _apply_suggestion_vote(band_id, suggestion_id):
    data = _payload(); value = _vote_value(data)
    if set(data) != {'value'}:
        raise ValidationError('Unsupported vote fields.')
    def prepare_response(scope, suggestion, viewer_vote):
        return jsonify({'suggestion': _suggestion_response(scope, suggestion, viewer_vote), 'viewer_vote': viewer_vote})
    return set_song_suggestion_vote(
        band_id=band_id, suggestion_id=suggestion_id, value=value, prepare_response=prepare_response,
    )


@content_writes_bp.put('/suggestions/<int:suggestion_id>/vote')
@authenticated_write
def put_suggestion_vote_v2(band_id, suggestion_id):
    return _apply_suggestion_vote(band_id, suggestion_id)


@content_writes_bp.patch('/suggestions/<int:suggestion_id>/vote')
@authenticated_write
def patch_suggestion_vote_v2(band_id, suggestion_id):
    return _apply_suggestion_vote(band_id, suggestion_id)


@content_writes_bp.post('/suggestions/<int:suggestion_id>/proceed')
@authenticated_write
def proceed_suggestion_v2(band_id, suggestion_id):
    data = _payload()
    # This endpoint accepts only a visibility decision; title/artist/link
    # always come from the reviewed suggestion and cannot be substituted.
    _check_fields(data, {'visibility'})
    def prepare_response(scope, song):
        return jsonify({'song': serialize_song(scope, song), 'proceeded': True}), 201
    return proceed_song_suggestion(
        band_id=band_id, suggestion_id=suggestion_id, actor=current_app_user(required=True),
        visibility=data.get('visibility'), prepare_response=prepare_response,
    )


@content_writes_bp.put('/announcement')
@authenticated_write
def put_announcement_v2(band_id):
    data = _payload(); _check_fields(data, _ANNOUNCEMENT_FIELDS)
    def prepare_response(scope, announcement):
        return jsonify({'announcement': serialize_announcement(scope, announcement)})
    return put_announcement(band_id=band_id, data=data, prepare_response=prepare_response)


@content_writes_bp.delete('/announcement')
@authenticated_write
def clear_announcement_v2(band_id):
    """Clear the singleton announcement synchronously; never enqueue text."""
    def prepare_response(scope, announcement):
        return jsonify({'announcement': serialize_announcement(scope, announcement), 'cleared': True})
    return clear_announcement(band_id=band_id, prepare_response=prepare_response)


@content_writes_bp.patch('/gallery/<int:image_id>/featured')
@authenticated_write
def set_gallery_featured_v2(band_id, image_id):
    """Set or clear a band's featured gallery image for active managers only."""
    data = _payload()
    if set(data) != {'is_featured'} or type(data['is_featured']) is not bool:
        raise ValidationError('is_featured must be a boolean.')
    desired = data['is_featured']

    def prepare_response(scope, image):
        return jsonify({'gallery': serialize_gallery_image(scope, image)})
    return set_gallery_featured(
        band_id=band_id, image_id=image_id, desired=desired, prepare_response=prepare_response,
    )


@content_writes_bp.put('/songs/<int:song_id>/featured-media')
@authenticated_write
def set_song_featured_media_v2(band_id, song_id):
    data = _payload()
    if set(data) != {'media_id'} or type(data['media_id']) is not int or data['media_id'] <= 0:
        raise ValidationError('media_id must be a positive integer.')
    media_id = data['media_id']
    return jsonify(set_song_featured_media(band_id=band_id, song_id=song_id, media_id=media_id))


@content_writes_bp.delete('/songs/<int:song_id>/featured-media')
@authenticated_write
def clear_song_featured_media_v2(band_id, song_id):
    return jsonify(clear_song_featured_media(band_id=band_id, song_id=song_id))
