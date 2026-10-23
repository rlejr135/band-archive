"""Authenticated, tenant-bound v2 mutations for core band content.

The v1 blueprints intentionally retain their device/password-based behaviour.
This module never delegates to them: an authenticated membership is the only
authority for these writes and all object lookups retain the URL band id.
"""

from datetime import date
from functools import wraps
import hashlib
import math

from flask import Blueprint, after_this_request, current_app, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from auth_service import current_app_user
from band_access import BandReadScope, MembershipAudience, RequestAudience, content_capabilities
from band_service import ACTIVE, MANAGER_ROLES, audit
from errors import NotFoundError, ValidationError
from extensions import db
from models import (Announcement, AppUser, Band, BandLogo, BandMembership, Comment, DirectUploadSession,
                    GalleryImage, Media, MediaVote, Member, MultipartUploadSession, PersonalLog,
                    PersonalLogMultipartUploadSession, R2DeletionTask, Rehearsal, Song,
                    SongSuggestion, SongSuggestionVote)
from validators import validate_external_http_url
from voc_validation import validate_rehearsal_type
from policy_service import confirm_public_expansion, upload_policy_enabled
from v2_content_serializers import (
    media_visibility, song_visibility, rehearsal_visibility, scope_with_band_visibility, serialize_comment, serialize_media,
    serialize_rehearsal, serialize_song,
)
from v2_remaining_read_serializers import personal_log_visibility, serialize_personal_comment
from v2_remaining_read_serializers import serialize_announcement, serialize_gallery_image, serialize_suggestion


content_writes_bp = Blueprint('content_writes', __name__, url_prefix='/api/v2/bands/<int:band_id>')

_VISIBILITIES = {'PUBLIC', 'MEMBERS'}
_SONG_FIELDS = {'title', 'artist', 'status', 'chords', 'link', 'memo', 'genre', 'difficulty', 'visibility', 'visibility_confirm'}
_REHEARSAL_FIELDS = {
    'title', 'date', 'start_date', 'end_date', 'time', 'location', 'latitude', 'longitude',
    'memo', 'color', 'visibility', 'song_ids', 'visibility_confirm', 'rehearsal_type',
}
_IGNORED_OWNERSHIP_FIELDS = {'band_id', 'created_by_membership_id', 'created_by_user_id', 'is_legacy'}
_SUGGESTION_FIELDS = {'title', 'artist', 'link', 'memo', 'visibility', 'visibility_confirm'}
_ANNOUNCEMENT_FIELDS = {'content', 'visibility', 'visibility_confirm'}


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
    from deletion_service import actor_is_eligible, load_current_actor, lock_band_and_membership
    from routes.content_reads import _creator_is_scoped_sql, _not_pending_delete
    from band_access import BandReadScope, MembershipAudience, RequestAudience

    data = _payload()
    if set(data) != {'gallery_image_id'}:
        raise ValidationError('Only gallery_image_id is accepted.')
    image_id = data['gallery_image_id']
    if image_id is not None and (type(image_id) is not int or image_id <= 0):
        raise ValidationError('gallery_image_id must be a positive integer or null.')
    user = current_app_user(required=True)

    def write():
        band, membership = lock_band_and_membership(band_id, user.id, require_member=False)
        actor = load_current_actor(user.id, lock=True)
        if band is None or actor is None or not actor_is_eligible(actor):
            raise NotFoundError('Resource not found')
        is_admin = actor.is_platform_admin is True
        is_owner = membership is not None and membership.role == 'owner'
        if not (is_owner or is_admin):
            raise NotFoundError('Resource not found')
        member_audience = MembershipAudience(
            id=membership.id, band_id=membership.band_id, role=membership.role, status=membership.status,
            user_subject=actor.supabase_user_id, user_display_name=actor.display_name, user_email=actor.email,
        ) if membership else None
        audience = RequestAudience(
            user_id=actor.id, subject=actor.supabase_user_id, account_active=True,
            email_verified=True, is_platform_admin=is_admin,
        )
        scope = scope_with_band_visibility(BandReadScope(
            band_id=band.id, band_slug=band.slug, audience=audience, membership=member_audience,
        ), band)
        image = None
        if image_id is not None:
            image_query = GalleryImage.query.filter(
                GalleryImage.id == image_id, GalleryImage.band_id == band.id,
                _not_pending_delete(GalleryImage, 'gallery'), _creator_is_scoped_sql(GalleryImage, scope),
            )
            if db.engine.dialect.name == 'postgresql':
                image_query = image_query.with_for_update()
            image = image_query.one_or_none()
            if image is None:
                raise NotFoundError('Resource not found')

        logo = BandLogo.query.filter_by(band_id=band.id)
        if db.engine.dialect.name == 'postgresql':
            logo = logo.with_for_update()
        logo = logo.one_or_none()
        previous_id = logo.gallery_image_id if logo else None
        if image_id is None:
            if logo is not None:
                db.session.delete(logo)
        elif logo is None:
            db.session.add(BandLogo(band_id=band.id, gallery_image_id=image_id))
        elif logo.gallery_image_id != image_id:
            logo.gallery_image_id = image_id
        db.session.flush()
        if previous_id != image_id:
            audit('band_logo_updated', actor, band=band, target=band,
                  details={'gallery_image_id': image_id, 'previous_gallery_image_id': previous_id})
        serialized = None
        if image_id is not None:
            try:
                value = serialize_gallery_image(scope, image) if image is not None else None
                serialized = {'gallery_image_id': image_id,
                              'url': value.get('url') if value else None}
            except Exception:
                serialized = {'gallery_image_id': image_id, 'url': None}
        return {'logo': serialized}

    return jsonify(_run_write(write))


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
    band_query = Band.query.filter(Band.id == band_id, Band.deleted_at.is_(None))
    membership_query = BandMembership.query.filter_by(band_id=band_id, user_id=user.id, status=ACTIVE)
    if lock and db.engine.dialect.name != 'sqlite':
        band_query = band_query.with_for_update()
        membership_query = membership_query.with_for_update()
    band = band_query.one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    membership = membership_query.one_or_none()
    if membership is None:
        raise ValidationError('An active band membership is required.', 403)
    if lock_actor:
        actor_query = AppUser.query.filter_by(id=user.id)
        if lock and db.engine.dialect.name != 'sqlite':
            actor_query = actor_query.with_for_update()
        user = actor_query.populate_existing().one_or_none()
        if user is None:
            raise ValidationError('An active band membership is required.', 403)
    audience = RequestAudience(
        user_id=user.id, subject=user.supabase_user_id,
        account_active=user.status == ACTIVE,
        email_verified=user.email_verified is True,
        is_platform_admin=user.is_platform_admin is True,
    )
    member_audience = MembershipAudience(
        id=membership.id, band_id=membership.band_id, role=membership.role, status=membership.status,
        user_subject=user.supabase_user_id, user_display_name=user.display_name, user_email=user.email,
    )
    return scope_with_band_visibility(BandReadScope(
        band_id=band.id, band_slug=band.slug, audience=audience, membership=member_audience,
    ), band), band, membership, user


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


def _text(value, field, maximum, *, required=False, nullable=True):
    if value is None:
        if required:
            raise ValidationError(f'{field} is required.')
        return None if nullable else value
    if not isinstance(value, str):
        raise ValidationError(f'{field} must be text.')
    value = value.strip()
    if required and not value:
        raise ValidationError(f'{field} is required.')
    if len(value) > maximum:
        raise ValidationError(f'{field} must be at most {maximum} characters.')
    return value or None


def _visibility(value, default):
    if value is None:
        return default
    if value not in _VISIBILITIES:
        raise ValidationError('visibility must be PUBLIC or MEMBERS.')
    return value


def _external_link(value, *, required=False):
    if value is None:
        if required:
            raise ValidationError('link is required.')
        return None
    return validate_external_http_url(value, 'link', 500)


def _date(value, field, *, required=False):
    if value is None:
        if required:
            raise ValidationError(f'{field} is required.')
        return None
    if not isinstance(value, str):
        raise ValidationError(f'{field} must be a valid date (YYYY-MM-DD).')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValidationError(f'{field} must be a valid date (YYYY-MM-DD).') from error


def _check_fields(data, allowed):
    unknown = set(data) - allowed - _IGNORED_OWNERSHIP_FIELDS
    if unknown:
        raise ValidationError('Unsupported field: ' + sorted(unknown)[0] + '.')


def _can_change(item, membership):
    return membership.role in MANAGER_ROLES or (
        item.created_by_membership_id is not None and item.created_by_membership_id == membership.id
    )


def _can_change_suggestion(item, membership):
    """Authors retain their own queue entry; managers only adopt legacy rows.

    Unlike songs, a manager must not silently rewrite a current member's
    recommendation.  A pre-v2 row has no author, so owner/admin stewardship
    is the only safe way to keep that historical queue maintainable.
    """
    return (
        item.created_by_membership_id == membership.id
        or (item.created_by_membership_id is None and membership.role in MANAGER_ROLES)
    )


def _can_delete(scope, item):
    """Use the same server-derived capability as read DTOs."""
    return content_capabilities(scope, item)['can_delete']


def _owned_or_404(model, record_id, band_id, message='Resource not found', *, lock=False):
    query = model.query.filter(model.id == record_id, model.band_id == band_id)
    resource_type = {
        Media: 'media', PersonalLog: 'personal_log', GalleryImage: 'gallery',
        Song: 'song', Rehearsal: 'rehearsal', Member: 'member', Comment: 'comment',
        SongSuggestion: 'suggestion', Announcement: 'announcement',
    }.get(model)
    if resource_type:
        query = query.filter(~db.session.query(R2DeletionTask.id).filter(
            R2DeletionTask.band_id == band_id,
            R2DeletionTask.resource_type == resource_type,
            R2DeletionTask.resource_id == model.id,
            R2DeletionTask.status != 'completed',
        ).exists())
    if lock and db.engine.dialect.name != 'sqlite':
        query = query.with_for_update()
    item = query.one_or_none()
    if item is None:
        raise NotFoundError(message)
    return item


def _songs_or_404(band_id, song_ids):
    if not isinstance(song_ids, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in song_ids):
        raise ValidationError('song_ids must be an array of integer IDs.')
    wanted = set(song_ids)
    songs = Song.query.filter(Song.band_id == band_id, Song.id.in_(wanted)).all() if wanted else []
    if len(songs) != len(wanted):
        raise NotFoundError('One or more songs are unavailable for this rehearsal.')
    return sorted(songs, key=lambda item: item.id)


def _assert_song_delete_safe(song):
    # ORM deletion cascades media and its comments/votes.  Refuse an entire
    # delete rather than allowing a damaged foreign tenant edge to cascade.
    if Media.query.filter(Media.song_id == song.id, Media.band_id != song.band_id).first() is not None:
        raise NotFoundError('Song not found')
    if Media.query.filter_by(song_id=song.id, band_id=song.band_id).first() is not None:
        raise ValidationError('Delete the song media individually before deleting this song.', 409)
    if song.sheet_music_object_key or song.sheet_music:
        raise ValidationError('Delete the song sheet music before deleting this song.', 409)
    live_multipart = MultipartUploadSession.query.filter(
        MultipartUploadSession.song_id == song.id,
        MultipartUploadSession.status.in_(('initiated', 'completing', 'expiring', 'aborting')),
    ).first()
    live_direct = DirectUploadSession.query.filter(
        DirectUploadSession.song_id == song.id,
        DirectUploadSession.status.in_(('initiated', 'completing', 'expiring')),
    ).first()
    if live_multipart or live_direct:
        raise ValidationError('Wait for attached uploads to finish cleanup before deleting this song.', 409)
    if any(rehearsal.band_id != song.band_id for rehearsal in song.rehearsals):
        raise NotFoundError('Song not found')


def _assert_rehearsal_delete_safe(rehearsal):
    if Media.query.filter(Media.rehearsal_id == rehearsal.id, Media.band_id != rehearsal.band_id).first() is not None:
        raise NotFoundError('Rehearsal not found')
    if Media.query.filter_by(rehearsal_id=rehearsal.id, band_id=rehearsal.band_id).first() is not None:
        raise ValidationError('Delete the rehearsal media individually before deleting this rehearsal.', 409)
    live_multipart = MultipartUploadSession.query.filter(
        MultipartUploadSession.rehearsal_id == rehearsal.id,
        MultipartUploadSession.status.in_(('initiated', 'completing', 'expiring', 'aborting')),
    ).first()
    live_direct = DirectUploadSession.query.filter(
        DirectUploadSession.rehearsal_id == rehearsal.id,
        DirectUploadSession.status.in_(('initiated', 'completing', 'expiring')),
    ).first()
    if live_multipart or live_direct:
        raise ValidationError('Wait for attached uploads to finish cleanup before deleting this rehearsal.', 409)
    if any(song.band_id != rehearsal.band_id for song in rehearsal.songs):
        raise NotFoundError('Rehearsal not found')


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


def _comment_target_kind(comment):
    if comment.media_id is not None and comment.personal_log_id is None:
        return 'media', comment.media_id
    if comment.personal_log_id is not None and comment.media_id is None:
        return 'personal_log', comment.personal_log_id
    raise NotFoundError('Resource not found')


def _assert_comment_tree_safe(comment):
    """Validate every cascade descendant before a delete touches the tree."""
    kind, target_id = _comment_target_kind(comment)
    pending, seen = [comment.id], set()
    while pending:
        node_id = pending.pop()
        if node_id in seen:
            raise NotFoundError('Resource not found')
        seen.add(node_id)
        node = _owned_or_404(Comment, node_id, comment.band_id, 'Resource not found')
        if _comment_target_kind(node) != (kind, target_id):
            raise NotFoundError('Resource not found')
        child_ids = [row[0] for row in db.session.query(Comment.id).filter(Comment.parent_id == node.id).all()]
        pending.extend(child_ids)


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
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        song = Song(
            band_id=band.id, created_by_membership_id=membership.id,
            visibility=_visibility(data.get('visibility'), band.default_visibility),
            title=_text(data.get('title'), 'title', 100, required=True),
            artist=_text(data.get('artist'), 'artist', 100, required=True),
        )
        _apply_song(song, data)
        db.session.add(song)
        db.session.flush()
        audit('content.song.created', user, band, song, {'visibility': song.visibility})
        return jsonify({'song': serialize_song(scope, song)}), 201
    return _run_write(operation)


def _apply_song(song, data):
    if 'title' in data:
        song.title = _text(data['title'], 'title', 100, required=True)
    if 'artist' in data:
        song.artist = _text(data['artist'], 'artist', 100, required=True)
    if 'status' in data:
        if data['status'] not in {'Practice', 'Completed', 'OnHold'}:
            raise ValidationError('Invalid status.')
        song.status = data['status']
    for field, maximum in (('chords', 10000), ('link', 200), ('memo', 10000), ('genre', 50)):
        if field in data:
            setattr(song, field, _text(data[field], field, maximum))
    if 'difficulty' in data:
        if isinstance(data['difficulty'], bool) or not isinstance(data['difficulty'], int) or not 1 <= data['difficulty'] <= 5:
            raise ValidationError('difficulty must be an integer between 1 and 5.')
        song.difficulty = data['difficulty']
    if 'visibility' in data:
        song.visibility = _visibility(data['visibility'], song.visibility)


@content_writes_bp.patch('/songs/<int:song_id>')
@authenticated_write
def update_song(band_id, song_id):
    data = _payload(); _check_fields(data, _SONG_FIELDS)
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        song = _owned_or_404(Song, song_id, band.id, 'Song not found', lock=True)
        if not _can_change(song, membership):
            raise ValidationError('You may only edit your own content.', 403)
        before_visibility = song_visibility(scope, song)
        _apply_song(song, data)
        if before_visibility == 'MEMBERS' and song_visibility(scope, song) == 'PUBLIC':
            confirm_public_expansion(data, user, band, song)
        audit('content.song.updated', user, band, song, {'fields': sorted(set(data) & _SONG_FIELDS)})
        return jsonify({'song': serialize_song(scope, song)})
    return _run_write(operation)


@content_writes_bp.delete('/songs/<int:song_id>')
@authenticated_write
def delete_song(band_id, song_id):
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        song = _owned_or_404(Song, song_id, band.id, 'Song not found', lock=True)
        if not _can_delete(scope, song):
            raise ValidationError('Only an active verified band owner or platform administrator may delete content.', 403)
        _assert_song_delete_safe(song)
        from deletion_service import cleanup_terminal_parent_upload_sessions
        if not cleanup_terminal_parent_upload_sessions('song', song):
            raise ValidationError('Upload history is not in a safe terminal state.', 409)
        audit('content.song.deleted', user, band, song)
        db.session.delete(song)
        return jsonify({'deleted': True, 'id': song_id})
    return _run_write(operation)


def _apply_rehearsal(rehearsal, data, band_id):
    if 'title' in data:
        rehearsal.title = _text(data['title'], 'title', 200, required=True)
    if 'date' in data:
        rehearsal.date = _date(data['date'], 'date', required=True)
    if 'start_date' in data:
        rehearsal.start_date = _date(data['start_date'], 'start_date')
    if 'end_date' in data:
        rehearsal.end_date = _date(data['end_date'], 'end_date')
    if rehearsal.start_date and rehearsal.end_date and rehearsal.start_date > rehearsal.end_date:
        raise ValidationError('start_date must be before or equal to end_date.')
    for field, maximum in (('time', 20), ('location', 200), ('memo', 10000), ('color', 7)):
        if field in data:
            setattr(rehearsal, field, _text(data[field], field, maximum))
    for field in ('latitude', 'longitude'):
        if field in data:
            value = data[field]
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))):
                raise ValidationError(f'{field} must be a finite number.')
            setattr(rehearsal, field, float(value) if value is not None else None)
    if 'visibility' in data:
        rehearsal.visibility = _visibility(data['visibility'], rehearsal.visibility)
    if 'rehearsal_type' in data:
        rehearsal.rehearsal_type = validate_rehearsal_type(data['rehearsal_type'])
    if 'song_ids' in data:
        # All IDs are resolved before replacing the relation: no partial link.
        rehearsal.songs = _songs_or_404(band_id, data['song_ids'])


@content_writes_bp.post('/rehearsals')
@authenticated_write
def create_rehearsal(band_id):
    data = _payload(); _check_fields(data, _REHEARSAL_FIELDS)
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        rehearsal = Rehearsal(
            band_id=band.id, created_by_membership_id=membership.id,
            visibility=_visibility(data.get('visibility'), band.default_visibility),
            title=_text(data.get('title'), 'title', 200, required=True),
            date=_date(data.get('date'), 'date', required=True),
        )
        _apply_rehearsal(rehearsal, data, band.id)
        db.session.add(rehearsal); db.session.flush()
        audit('content.rehearsal.created', user, band, rehearsal, {'visibility': rehearsal.visibility})
        return jsonify({'rehearsal': serialize_rehearsal(scope, rehearsal)}), 201
    return _run_write(operation)


@content_writes_bp.patch('/rehearsals/<int:rehearsal_id>')
@authenticated_write
def update_rehearsal(band_id, rehearsal_id):
    data = _payload(); _check_fields(data, _REHEARSAL_FIELDS)
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        rehearsal = _owned_or_404(Rehearsal, rehearsal_id, band.id, 'Rehearsal not found', lock=True)
        if not _can_change(rehearsal, membership):
            raise ValidationError('You may only edit your own content.', 403)
        old_visibility = rehearsal_visibility(scope, rehearsal)
        affected = []
        before = {}
        if upload_policy_enabled() and 'song_ids' in data:
            song_ids = {song.id for song in rehearsal.songs}
            song_ids.update(song.id for song in _songs_or_404(band.id, data['song_ids']))
            affected = Media.query.filter(Media.band_id == band.id, Media.song_id.in_(song_ids)).all()
            before = {item.id: media_visibility(scope, item) for item in affected}
        _apply_rehearsal(rehearsal, data, band.id)
        db.session.flush()
        expands = old_visibility == 'MEMBERS' and rehearsal_visibility(scope, rehearsal) == 'PUBLIC'
        if affected:
            for song_id in {item.song_id for item in affected}:
                song = db.session.get(Song, song_id)
                db.session.expire(song, ['rehearsals'])
            expands = expands or any(before[item.id] == 'MEMBERS' and media_visibility(scope, item) == 'PUBLIC'
                                     for item in affected)
        if expands:
            confirm_public_expansion(data, user, band, rehearsal)
        audit('content.rehearsal.updated', user, band, rehearsal, {'fields': sorted(set(data) & _REHEARSAL_FIELDS)})
        return jsonify({'rehearsal': serialize_rehearsal(scope, rehearsal)})
    return _run_write(operation)


@content_writes_bp.delete('/rehearsals/<int:rehearsal_id>')
@authenticated_write
def delete_rehearsal(band_id, rehearsal_id):
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        rehearsal = _owned_or_404(Rehearsal, rehearsal_id, band.id, 'Rehearsal not found', lock=True)
        if not _can_delete(scope, rehearsal):
            raise ValidationError('Only an active verified band owner or platform administrator may delete content.', 403)
        _assert_rehearsal_delete_safe(rehearsal)
        from deletion_service import cleanup_terminal_parent_upload_sessions
        if not cleanup_terminal_parent_upload_sessions('rehearsal', rehearsal):
            raise ValidationError('Upload history is not in a safe terminal state.', 409)
        audit('content.rehearsal.deleted', user, band, rehearsal)
        db.session.delete(rehearsal)
        return jsonify({'deleted': True, 'id': rehearsal_id})
    return _run_write(operation)


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


def _apply_suggestion(suggestion, data):
    if 'title' in data:
        suggestion.title = _text(data['title'], 'title', 100, required=True)
    if 'artist' in data:
        suggestion.artist = _text(data['artist'], 'artist', 100, required=True)
    if 'link' in data:
        suggestion.link = _external_link(data['link'], required=True)
    if 'memo' in data:
        suggestion.memo = _text(data['memo'], 'memo', 10000)
    if 'visibility' in data:
        suggestion.visibility = _visibility(data['visibility'], suggestion.visibility)


@content_writes_bp.post('/suggestions')
@authenticated_write
def create_suggestion_v2(band_id):
    data = _payload(); _check_fields(data, _SUGGESTION_FIELDS)
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        suggestion = SongSuggestion(
            band_id=band.id, created_by_membership_id=membership.id,
            visibility=_visibility(data.get('visibility'), band.default_visibility),
            title=_text(data.get('title'), 'title', 100, required=True),
            artist=_text(data.get('artist'), 'artist', 100, required=True),
            link=_external_link(data.get('link'), required=True),
        )
        _apply_suggestion(suggestion, data)
        db.session.add(suggestion); db.session.flush()
        audit('content.suggestion.created', user, band, suggestion, {'visibility': suggestion.visibility})
        return jsonify({'suggestion': _suggestion_response(scope, suggestion)}), 201
    return _run_write(operation)


@content_writes_bp.patch('/suggestions/<int:suggestion_id>')
@authenticated_write
def update_suggestion_v2(band_id, suggestion_id):
    data = _payload(); _check_fields(data, _SUGGESTION_FIELDS)
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        suggestion = _suggestion_or_404(suggestion_id, band.id, lock=True)
        if not _can_change_suggestion(suggestion, membership):
            raise ValidationError('You may only edit your own suggestion.', 403)
        if band.default_visibility == 'PUBLIC' and suggestion.visibility == 'MEMBERS' and data.get('visibility') == 'PUBLIC':
            confirm_public_expansion(data, user, band, suggestion)
        _apply_suggestion(suggestion, data)
        audit('content.suggestion.updated', user, band, suggestion,
              {'fields': sorted(set(data) & _SUGGESTION_FIELDS)})
        return jsonify({'suggestion': _suggestion_response(scope, suggestion)})
    return _run_write(operation)


@content_writes_bp.delete('/suggestions/<int:suggestion_id>')
@authenticated_write
def delete_suggestion_v2(band_id, suggestion_id):
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        suggestion = _suggestion_or_404(suggestion_id, band.id, lock=True)
        if not _can_delete(scope, suggestion):
            raise ValidationError('Only an active verified band owner or platform administrator may delete content.', 403)
        audit('content.suggestion.deleted', user, band, suggestion)
        db.session.delete(suggestion)
        return jsonify({'deleted': True, 'id': suggestion_id})
    return _run_write(operation)


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
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        if membership.role not in MANAGER_ROLES:
            raise ValidationError('Insufficient band role.', 403)
        suggestion = _suggestion_or_404(suggestion_id, band.id, lock=True)
        if not _can_delete(scope, suggestion):
            raise ValidationError('Only an active verified band owner or platform administrator may delete content.', 403)
        song = Song(
            band_id=band.id, created_by_membership_id=membership.id,
            visibility=_visibility(data.get('visibility'), suggestion.visibility or band.default_visibility),
            title=suggestion.title, artist=suggestion.artist,
            link=_external_link(suggestion.link, required=True),
            memo=suggestion.memo, status='Practice',
        )
        db.session.add(song); db.session.flush()
        audit('content.suggestion.proceeded', user, band, song, {'suggestion_id': suggestion.id,
                                                                   'visibility': song.visibility})
        db.session.delete(suggestion)
        return jsonify({'song': serialize_song(scope, song), 'proceeded': True}), 201
    return _run_write(operation)


@content_writes_bp.put('/announcement')
@authenticated_write
def put_announcement_v2(band_id):
    data = _payload(); _check_fields(data, _ANNOUNCEMENT_FIELDS)
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        if membership.role not in MANAGER_ROLES:
            raise ValidationError('Insufficient band role.', 403)
        query = Announcement.query.filter_by(band_id=band.id)
        if db.engine.dialect.name != 'sqlite':
            query = query.with_for_update()
        rows = query.order_by(Announcement.id.asc()).limit(2).all()
        if len(rows) > 1:
            raise NotFoundError('Announcement not found')
        announcement = rows[0] if rows else Announcement(
            band_id=band.id, created_by_membership_id=membership.id,
            visibility=_visibility(data.get('visibility'), band.default_visibility),
        )
        if 'content' not in data:
            raise ValidationError('content is required.')
        if rows and band.default_visibility == 'PUBLIC' and announcement.visibility == 'MEMBERS' and data.get('visibility') == 'PUBLIC':
            confirm_public_expansion(data, user, band, announcement)
        announcement.content = _text(data['content'], 'content', 10000, required=True)
        if 'visibility' in data:
            announcement.visibility = _visibility(data['visibility'], announcement.visibility)
        if not rows:
            db.session.add(announcement); db.session.flush()
            audit('content.announcement.created', user, band, announcement,
                  {'visibility': announcement.visibility})
        else:
            audit('content.announcement.updated', user, band, announcement,
                  {'fields': sorted(set(data) & _ANNOUNCEMENT_FIELDS)})
        return jsonify({'announcement': serialize_announcement(scope, announcement)})
    return _run_write(operation)


@content_writes_bp.delete('/announcement')
@authenticated_write
def clear_announcement_v2(band_id):
    """Clear the singleton announcement synchronously; never enqueue text."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        if membership.role != 'owner' and user.is_platform_admin is not True:
            raise ValidationError('Only an active verified owner may clear the announcement.', 403)
        rows = Announcement.query.filter_by(band_id=band.id).order_by(Announcement.id.asc()).limit(2).all()
        if len(rows) != 1:
            raise NotFoundError('Announcement not found')
        announcement = rows[0]
        announcement.content = ''
        audit('content.announcement.cleared', user, band, announcement)
        return jsonify({'announcement': serialize_announcement(scope, announcement), 'cleared': True})
    return _run_write(operation)


@content_writes_bp.patch('/gallery/<int:image_id>/featured')
@authenticated_write
def set_gallery_featured_v2(band_id, image_id):
    """Set or clear a band's featured gallery image for active managers only."""
    data = _payload()
    if set(data) != {'is_featured'} or type(data['is_featured']) is not bool:
        raise ValidationError('is_featured must be a boolean.')
    desired = data['is_featured']

    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        if membership.role not in MANAGER_ROLES:
            raise ValidationError('Insufficient band role.', 403)

        image = _owned_or_404(
            GalleryImage, image_id, band.id, 'Gallery image not found', lock=True,
        )
        # A NULL creator is a valid legacy row. Any non-NULL creator pointer
        # must remain in this tenant before the image can be changed or signed.
        if image.created_by_membership_id is not None:
            creator = BandMembership.query.filter_by(
                id=image.created_by_membership_id, band_id=band.id,
            ).one_or_none()
            if creator is None:
                raise NotFoundError('Gallery image not found')
        if image.visibility not in _VISIBILITIES:
            raise NotFoundError('Gallery image not found')

        featured_query = GalleryImage.query.filter(
            GalleryImage.band_id == band.id,
            GalleryImage.is_featured.is_(True),
        )
        if db.engine.dialect.name != 'sqlite':
            featured_query = featured_query.with_for_update()
        featured_rows = featured_query.all()
        before = bool(image.is_featured)
        changed = False
        cleared = 0
        if desired:
            other_featured = featured_query.filter(GalleryImage.id != image.id)
            cleared = sum(existing.id != image.id for existing in featured_rows)
            if cleared:
                # Apply the clears before setting the selected row so the
                # partial unique index remains satisfied in either ID order.
                other_featured.update({GalleryImage.is_featured: False}, synchronize_session='fetch')
                changed = True
            if not before:
                image.is_featured = True
                changed = True
        elif before:
            image.is_featured = False
            changed = True

        if changed:
            audit('content.gallery.featured.changed', user, band, image,
                  {'is_featured': desired, 'cleared_count': cleared})
        return jsonify({'gallery': serialize_gallery_image(scope, image)})

    return _run_write(operation)


@content_writes_bp.put('/songs/<int:song_id>/featured-media')
@authenticated_write
def set_song_featured_media_v2(band_id, song_id):
    data = _payload()
    if set(data) != {'media_id'} or type(data['media_id']) is not int or data['media_id'] <= 0:
        raise ValidationError('media_id must be a positive integer.')
    media_id = data['media_id']

    def operation():
        from deletion_service import actor_is_eligible, load_current_actor, lock_band_and_membership
        from media_processing import select_media_source_keys
        from content_restrictions import require_unrestricted
        from v2_content_serializers import graph_is_scoped

        principal = current_app_user(required=True)
        band, membership = lock_band_and_membership(band_id, principal.id, require_member=False)
        if band is None:
            raise NotFoundError('Band not found')
        actor = load_current_actor(principal.id, lock=True)
        if not actor_is_eligible(actor):
            raise ValidationError('An active, verified account is required.', 403)
        if not (actor.is_platform_admin is True or (membership is not None and membership.role == 'owner')):
            raise ValidationError('Only a band owner or platform administrator may feature media.', 403)

        audience = RequestAudience(
            user_id=actor.id, subject=actor.supabase_user_id, account_active=True,
            email_verified=True, is_platform_admin=actor.is_platform_admin is True,
        )
        member_audience = MembershipAudience(
            id=membership.id, band_id=membership.band_id, role=membership.role, status=membership.status,
            user_subject=actor.supabase_user_id, user_display_name=actor.display_name, user_email=actor.email,
        ) if membership is not None else None
        scope = scope_with_band_visibility(BandReadScope(
            band_id=band.id, band_slug=band.slug, audience=audience, membership=member_audience,
        ), band)

        song_query = Song.query.filter_by(id=song_id, band_id=band.id)
        if db.engine.dialect.name != 'sqlite':
            song_query = song_query.with_for_update()
        song = song_query.populate_existing().one_or_none()
        if song is None:
            raise NotFoundError('Resource not found')
        require_unrestricted(song)
        if song.visibility not in _VISIBILITIES:
            raise NotFoundError('Resource not found')

        from routes.content_reads import _media_pending_deletes_sql, _media_playable_sql, _not_pending_delete
        if db.session.query(_not_pending_delete(Song, 'song')).filter(Song.id == song.id).scalar() is not True:
            raise NotFoundError('Resource not found')
        candidate_query = Media.query.options(
            joinedload(Media.rehearsal), joinedload(Media.song).selectinload(Song.rehearsals),
        ).filter(
            Media.id == media_id, Media.band_id == band.id, Media.song_id == song.id,
            _media_playable_sql(), *_media_pending_deletes_sql(),
        )
        candidate = candidate_query.one_or_none()
        if candidate is None or not graph_is_scoped(scope, candidate):
            raise NotFoundError('Resource not found')
        require_unrestricted(candidate)
        for rehearsal in [candidate.rehearsal, *candidate.song.rehearsals]:
            if rehearsal is not None:
                require_unrestricted(rehearsal)
        if candidate.visibility not in _VISIBILITIES or any(
                rehearsal.visibility not in _VISIBILITIES
                for rehearsal in [candidate.rehearsal, *candidate.song.rehearsals]
                if rehearsal is not None):
            raise NotFoundError('Resource not found')
        source_keys = select_media_source_keys(candidate, 'media')
        playable = (
            candidate.file_type == 'video' and bool(source_keys['video_720'] or source_keys['original'])
        ) or (candidate.file_type == 'audio' and bool(source_keys['original']))
        if not playable:
            raise NotFoundError('Resource not found')

        previous = Media.query.filter_by(
            band_id=band.id, song_id=song.id, is_featured=True,
        ).order_by(Media.id.asc()).all()
        changed = candidate.is_featured is not True or any(item.id != candidate.id for item in previous)
        if changed:
            for item in previous:
                if item.id != candidate.id:
                    item.is_featured = False
            db.session.flush()
            candidate.is_featured = True
            db.session.flush()
            audit('content.song.featured_media.changed', actor, band, candidate,
                  {'song_id': song.id, 'media_id': candidate.id,
                   'cleared_count': sum(item.id != candidate.id for item in previous)})
        # Do not expose private playback URLs in this action response.  Reads
        # use the normal visibility-aware serializer and signed URL TTLs.
        return {'song_id': song.id, 'media_id': candidate.id, 'is_featured': True, 'can_feature': True}

    return jsonify(_run_write(operation))


@content_writes_bp.delete('/songs/<int:song_id>/featured-media')
@authenticated_write
def clear_song_featured_media_v2(band_id, song_id):
    def operation():
        from deletion_service import actor_is_eligible, load_current_actor, lock_band_and_membership
        from content_restrictions import require_unrestricted
        from routes.content_reads import _not_pending_delete

        principal = current_app_user(required=True)
        band, membership = lock_band_and_membership(band_id, principal.id, require_member=False)
        if band is None:
            raise NotFoundError('Band not found')
        actor = load_current_actor(principal.id, lock=True)
        if not actor_is_eligible(actor):
            raise ValidationError('An active, verified account is required.', 403)
        if not (actor.is_platform_admin is True or (membership is not None and membership.role == 'owner')):
            raise ValidationError('Only a band owner or platform administrator may feature media.', 403)
        song_query = Song.query.filter_by(id=song_id, band_id=band.id)
        if db.engine.dialect.name != 'sqlite':
            song_query = song_query.with_for_update()
        song = song_query.populate_existing().one_or_none()
        if song is None:
            raise NotFoundError('Resource not found')
        require_unrestricted(song)
        if db.session.query(_not_pending_delete(Song, 'song')).filter(Song.id == song.id).scalar() is not True:
            raise NotFoundError('Resource not found')
        rows = Media.query.filter_by(
            band_id=band.id, song_id=song.id, is_featured=True,
        ).order_by(Media.id.asc()).all()
        for item in rows:
            item.is_featured = False
        if rows:
            db.session.flush()
            audit('content.song.featured_media.cleared', actor, band, song,
                  {'song_id': song.id, 'cleared_count': len(rows)})
        return {'song_id': song.id, 'media': None, 'cleared': bool(rows), 'can_feature': True}

    return jsonify(_run_write(operation))
