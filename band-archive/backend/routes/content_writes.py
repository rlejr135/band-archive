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

from auth_service import current_app_user
from band_access import BandReadScope, MembershipAudience, RequestAudience
from band_service import ACTIVE, MANAGER_ROLES, audit
from errors import NotFoundError, ValidationError
from extensions import db
from models import Band, BandMembership, Comment, Media, MediaVote, PersonalLog, Rehearsal, Song
from v2_content_serializers import (
    media_visibility, scope_with_band_visibility, serialize_comment, serialize_media,
    serialize_rehearsal, serialize_song,
)
from v2_remaining_read_serializers import personal_log_visibility, serialize_personal_comment


content_writes_bp = Blueprint('content_writes', __name__, url_prefix='/api/v2/bands/<int:band_id>')

_VISIBILITIES = {'PUBLIC', 'MEMBERS'}
_SONG_FIELDS = {'title', 'artist', 'status', 'chords', 'link', 'memo', 'genre', 'difficulty', 'visibility'}
_REHEARSAL_FIELDS = {
    'title', 'date', 'start_date', 'end_date', 'time', 'location', 'latitude', 'longitude',
    'memo', 'color', 'visibility', 'song_ids',
}
_IGNORED_OWNERSHIP_FIELDS = {'band_id', 'created_by_membership_id', 'created_by_user_id', 'is_legacy'}


@content_writes_bp.before_request
def require_content_write_schema():
    if not current_app.config.get('BAND_API_SCHEMA_READY', False):
        return jsonify({
            'error': 'band_schema_unavailable',
            'message': 'Band API requires the isolated Alembic migration before startup.',
        }), 503


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


def _payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValidationError('A JSON object is required.')
    return value


def _scope_for_write(band_id, *, lock=False):
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
    audience = RequestAudience(user_id=user.id, subject=user.supabase_user_id)
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


def _owned_or_404(model, record_id, band_id, message='Resource not found', *, lock=False):
    query = model.query.filter(model.id == record_id, model.band_id == band_id)
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
    if any(rehearsal.band_id != song.band_id for rehearsal in song.rehearsals):
        raise NotFoundError('Song not found')


def _assert_rehearsal_delete_safe(rehearsal):
    if Media.query.filter(Media.rehearsal_id == rehearsal.id, Media.band_id != rehearsal.band_id).first() is not None:
        raise NotFoundError('Rehearsal not found')
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
        _apply_song(song, data)
        audit('content.song.updated', user, band, song, {'fields': sorted(set(data) & _SONG_FIELDS)})
        return jsonify({'song': serialize_song(scope, song)})
    return _run_write(operation)


@content_writes_bp.delete('/songs/<int:song_id>')
@authenticated_write
def delete_song(band_id, song_id):
    def operation():
        _scope, band, membership, user = _scope_for_write(band_id, lock=True)
        song = _owned_or_404(Song, song_id, band.id, 'Song not found', lock=True)
        if not _can_change(song, membership):
            raise ValidationError('You may only delete your own content.', 403)
        _assert_song_delete_safe(song)
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
        _apply_rehearsal(rehearsal, data, band.id)
        audit('content.rehearsal.updated', user, band, rehearsal, {'fields': sorted(set(data) & _REHEARSAL_FIELDS)})
        return jsonify({'rehearsal': serialize_rehearsal(scope, rehearsal)})
    return _run_write(operation)


@content_writes_bp.delete('/rehearsals/<int:rehearsal_id>')
@authenticated_write
def delete_rehearsal(band_id, rehearsal_id):
    def operation():
        _scope, band, membership, user = _scope_for_write(band_id, lock=True)
        rehearsal = _owned_or_404(Rehearsal, rehearsal_id, band.id, 'Rehearsal not found', lock=True)
        if not _can_change(rehearsal, membership):
            raise ValidationError('You may only delete your own content.', 403)
        _assert_rehearsal_delete_safe(rehearsal)
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
        _scope, band, membership, user = _scope_for_write(band_id, lock=True)
        comment, kind, target = _load_comment_and_target(comment_id, band.id, lock=True)
        if not _can_change(comment, membership):
            raise ValidationError('You may only delete your own content.', 403)
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
                    if previous != value:
                        row.value = value
            media.upvote_count = max(0, (media.upvote_count or 0) + int(value == 1) - int(previous == 1))
            media.downvote_count = max(0, (media.downvote_count or 0) + int(value == -1) - int(previous == -1))
            media.vote_score = media.upvote_count - media.downvote_count
        db.session.flush()
        audit('content.media_vote.changed', user, band, media, {'value': value, 'changed': previous != value})
        return jsonify({'media': serialize_media(scope, media), 'viewer_vote': value})
    return _run_write(operation)


@content_writes_bp.put('/media/<int:media_id>/vote')
@authenticated_write
def put_media_vote_v2(band_id, media_id):
    return _apply_media_vote(band_id, media_id)


@content_writes_bp.patch('/media/<int:media_id>/vote')
@authenticated_write
def patch_media_vote_v2(band_id, media_id):
    return _apply_media_vote(band_id, media_id)
