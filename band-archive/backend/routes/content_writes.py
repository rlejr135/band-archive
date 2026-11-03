"""Authenticated, tenant-bound v2 mutations for core band content.

The v1 blueprints intentionally retain their device/password-based behaviour.
This module never delegates to them: an authenticated membership is the only
authority for these writes and all object lookups retain the URL band id.
"""

from functools import wraps
import hashlib
import math

from flask import Blueprint, after_this_request, current_app, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auth_service import current_app_user
from announcement_service import (
    ANNOUNCEMENT_FIELDS as _ANNOUNCEMENT_FIELDS,
    clear_announcement, put_announcement,
)
from band_access import content_capabilities
from band_logo_service import set_band_logo as update_band_logo_selection
from band_service import ACTIVE, audit
from comment_rules import _assert_comment_tree_safe, _comment_target_kind, _owned_or_404
from content_write_scope import active_member_write_scope
from content_write_rules import can_change_authored_content as _can_change
from content_write_values import (
    external_link_value as _external_link,
    text_value as _text,
    visibility_value as _visibility,
)
from content_visibility import (
    media_visibility, personal_log_visibility,
)
from errors import NotFoundError, ValidationError
from extensions import db
from featured_media_service import clear_song_featured_media, set_song_featured_media
from gallery_featured_service import set_gallery_featured
from models import (AppUser, Band, BandMembership, Comment,
                    Media, MediaVote, Member, PersonalLog,
                    PersonalLogMultipartUploadSession, R2DeletionTask,
                    SongSuggestion, SongSuggestionVote)
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


def _scope_for_write(band_id, *, lock=False, lock_actor=False):
    """Return an active-member scope; writes use 403 for a known nonmember."""
    user = current_app_user(required=True)
    return active_member_write_scope(band_id, user, lock=lock, lock_actor=lock_actor)


def _begin_write():
    # SQLite needs an explicit writer lock before checking mutable authority;
    # PostgreSQL locks are obtained by _scope_for_write / target lookups.
    if db.engine.dialect.name == 'sqlite':
        db.session.execute(text('BEGIN IMMEDIATE'))


def _run_write(callback):
    try:
        _begin_write()
        result = callback()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def _check_fields(data, allowed):
    unknown = set(data) - allowed - _IGNORED_OWNERSHIP_FIELDS
    if unknown:
        raise ValidationError('Unsupported field: ' + sorted(unknown)[0] + '.')


def _can_delete(scope, item):
    """Use the same server-derived capability as read DTOs."""
    return content_capabilities(scope, item)['can_delete']


def _target_for_comment(kind, target_id, band_id, *, lock=False):
    if kind == 'media':
        target = _owned_or_404(Media, target_id, band_id, 'Resource not found', lock=lock)
        if target.song is None or target.song.band_id != band_id:
            raise NotFoundError('Resource not found')
        if target.rehearsal is not None and target.rehearsal.band_id != band_id:
            raise NotFoundError('Resource not found')
        return target
    target = _owned_or_404(PersonalLog, target_id, band_id, 'Resource not found', lock=lock)
    if target.member is None or target.member.band_id != band_id:
        raise NotFoundError('Resource not found')
    return target


def _comment_parent_or_404(parent_id, kind, target_id, band_id):
    if parent_id is None:
        return None
    if isinstance(parent_id, bool) or not isinstance(parent_id, int):
        raise ValidationError('parent_id must be an integer.')
    parent = _owned_or_404(Comment, parent_id, band_id, 'Resource not found')
    if _comment_target_kind(parent) != (kind, target_id):
        raise NotFoundError('Resource not found')
    # Existing malformed loops must never become a v2 thread parent.
    seen, cursor = set(), parent
    while cursor is not None:
        if cursor.id in seen:
            raise NotFoundError('Resource not found')
        seen.add(cursor.id)
        cursor = _owned_or_404(Comment, cursor.parent_id, band_id, 'Resource not found') if cursor.parent_id else None
    return parent


def _timestamp(data, *, parent, target):
    if 'timestamp_seconds' not in data:
        return None, False
    value = data['timestamp_seconds']
    if value is None:
        return None, True
    if parent is not None or getattr(target, 'file_type', None) not in {'audio', 'video'}:
        raise ValidationError('Timestamps are only available for top-level audio or video comments.')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        raise ValidationError('timestamp_seconds must be a non-negative finite number.')
    return float(value), True


def _author(user):
    # Display name is account metadata, not request-supplied impersonation.
    return (user.display_name or user.email).strip()[:50] or 'Member'


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
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        target = _target_for_comment(kind, target_id, band.id, lock=True)
        parent_id = data.get('parent_id')
        parent = _comment_parent_or_404(parent_id, kind, target.id, band.id)
        timestamp_seconds, _ = _timestamp(data, parent=parent, target=target)
        content = _text(data.get('content'), 'content', 10000, required=True)
        values = {'band_id': band.id, 'created_by_membership_id': membership.id, 'is_legacy': False,
                  'parent_id': parent.id if parent else None, 'author': _author(user), 'content': content,
                  'timestamp_seconds': timestamp_seconds}
        values['media_id' if kind == 'media' else 'personal_log_id'] = target.id
        comment = Comment(**values)
        db.session.add(comment); db.session.flush()
        audit('content.comment.created', user, band, comment, {'target': kind, 'target_id': target.id})
        return jsonify({'comment': _comment_response(scope, comment, target)}), 201
    return _run_write(operation)


@content_writes_bp.post('/media/<int:media_id>/comments')
@authenticated_write
def create_media_comment_v2(band_id, media_id):
    return _create_comment(band_id, 'media', media_id)


@content_writes_bp.post('/personal-logs/<int:log_id>/comments')
@authenticated_write
def create_personal_log_comment_v2(band_id, log_id):
    return _create_comment(band_id, 'personal_log', log_id)


def _load_comment_and_target(comment_id, band_id, *, lock=False):
    comment = _owned_or_404(Comment, comment_id, band_id, 'Comment not found', lock=lock)
    kind, target_id = _comment_target_kind(comment)
    target = _target_for_comment(kind, target_id, band_id, lock=lock)
    return comment, kind, target


@content_writes_bp.patch('/comments/<int:comment_id>')
@authenticated_write
def update_comment_v2(band_id, comment_id):
    data = _payload()
    allowed = {'content', 'timestamp_seconds'} | _IGNORED_OWNERSHIP_FIELDS | {'author', 'password', 'parent_id'}
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError('Unsupported field: ' + sorted(unknown)[0] + '.')
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        comment, kind, target = _load_comment_and_target(comment_id, band.id, lock=True)
        if not _can_change(comment, membership):
            raise ValidationError('You may only edit your own content.', 403)
        if 'content' in data:
            comment.content = _text(data['content'], 'content', 10000, required=True)
        if 'timestamp_seconds' in data:
            comment.timestamp_seconds, _ = _timestamp(data, parent=comment.parent, target=target)
        audit('content.comment.updated', user, band, comment, {'target': kind})
        return jsonify({'comment': _comment_response(scope, comment, target)})
    return _run_write(operation)


@content_writes_bp.delete('/comments/<int:comment_id>')
@authenticated_write
def delete_comment_v2(band_id, comment_id):
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        comment, kind, target = _load_comment_and_target(comment_id, band.id, lock=True)
        if not _can_delete(scope, comment):
            raise ValidationError('Only an active verified band owner or platform administrator may delete content.', 403)
        _assert_comment_tree_safe(comment)
        audit('content.comment.deleted', user, band, comment, {'target': kind, 'target_id': target.id})
        db.session.delete(comment)
        return jsonify({'deleted': True, 'id': comment_id})
    return _run_write(operation)


def _v2_voter_hash(membership_id):
    # Namespacing prevents account votes from ever becoming a legacy device
    # identity, while the digest preserves the existing fixed-width schema.
    return hashlib.sha256(f'v2-membership:{membership_id}'.encode('utf-8')).hexdigest()


def _vote_value(data):
    value = data.get('value')
    if isinstance(value, bool) or not isinstance(value, int) or value not in {-1, 0, 1}:
        raise ValidationError('value must be -1, 0, or 1.')
    return value


def _apply_media_vote(band_id, media_id):
    data = _payload(); value = _vote_value(data)
    if set(data) - {'value'}:
        raise ValidationError('Unsupported vote fields.')
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        media = _target_for_comment('media', media_id, band.id, lock=True)
        # Locking the parent media serializes inserts as well as updates on
        # PostgreSQL; BEGIN IMMEDIATE does the same on SQLite.
        row = MediaVote.query.filter_by(media_id=media.id, created_by_membership_id=membership.id).one_or_none()
        previous = row.value if row else 0
        if previous != value:
            if row is not None and value == 0:
                db.session.delete(row)
            elif row is not None:
                row.value = value
            elif value:
                # The parent media lock serializes normal v2 callers.  The
                # savepoint is a second line of defence for a direct writer or
                # a transaction that began before it acquired that lock: after
                # a uniqueness race, reread the authoritative account vote
                # and apply the requested state exactly once.
                try:
                    with db.session.begin_nested():
                        row = MediaVote(
                            media_id=media.id, band_id=band.id, created_by_membership_id=membership.id,
                            is_legacy=False, voter_hash=_v2_voter_hash(membership.id), value=value,
                        )
                        db.session.add(row)
                        db.session.flush()
                except IntegrityError:
                    row = MediaVote.query.filter_by(
                        media_id=media.id, created_by_membership_id=membership.id,
                    ).one_or_none()
                    if row is None:
                        raise
                    previous = row.value
                    row.created_by_membership_id = membership.id
                    if previous != value:
                        row.value = value
            media.upvote_count = max(0, (media.upvote_count or 0) + int(value == 1) - int(previous == 1))
            media.downvote_count = max(0, (media.downvote_count or 0) + int(value == -1) - int(previous == -1))
            media.vote_score = media.upvote_count - media.downvote_count
        db.session.flush()
        audit('content.media_vote.changed', user, band, media, {'value': value, 'changed': previous != value})
        return jsonify({'media': serialize_media(scope, media, value), 'viewer_vote': value})
    return _run_write(operation)


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


def _suggestion_or_404(suggestion_id, band_id, *, lock=False):
    return _owned_or_404(SongSuggestion, suggestion_id, band_id, 'Suggestion not found', lock=lock)


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
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        suggestion = _suggestion_or_404(suggestion_id, band.id, lock=True)
        row = SongSuggestionVote.query.filter_by(
            suggestion_id=suggestion.id, voter_user_id=user.id,
        ).one_or_none()
        previous = row.value if row else 0
        # The account is the vote identity; retain the newest active
        # membership only as mutable audit context after a leave/rejoin.
        if row is not None:
            row.created_by_membership_id = membership.id
        if previous != value:
            if row is not None and value == 0:
                db.session.delete(row)
            elif row is not None:
                row.value = value
            elif value:
                # The locked suggestion serializes ordinary PG callers.  The
                # savepoint makes direct or stale concurrent inserts converge
                # on one membership row without double-counting the totals.
                try:
                    with db.session.begin_nested():
                        row = SongSuggestionVote(
                            band_id=band.id, suggestion_id=suggestion.id,
                            voter_user_id=user.id, created_by_membership_id=membership.id,
                            is_legacy=False, value=value,
                        )
                        db.session.add(row); db.session.flush()
                except IntegrityError:
                    row = SongSuggestionVote.query.filter_by(
                        suggestion_id=suggestion.id, voter_user_id=user.id,
                    ).one_or_none()
                    if row is None:
                        raise
                    previous = row.value
                    if previous != value:
                        row.value = value
            suggestion.thumbs_up = max(0, (suggestion.thumbs_up or 0) + int(value == 1) - int(previous == 1))
            suggestion.thumbs_down = max(0, (suggestion.thumbs_down or 0) + int(value == -1) - int(previous == -1))
        db.session.flush()
        audit('content.suggestion_vote.changed', user, band, suggestion,
              {'value': value, 'changed': previous != value})
        return jsonify({'suggestion': _suggestion_response(scope, suggestion, value), 'viewer_vote': value})
    return _run_write(operation)


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
