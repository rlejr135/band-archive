"""Atomic Rehearsal writes and their Song association visibility checks."""

from datetime import date
import math

from sqlalchemy import text

from auth_service import current_app_user
from band_access import content_capabilities
from band_service import audit
from comment_rules import _owned_or_404
from content_write_rules import can_change_authored_content as _can_change
from content_write_scope import active_member_write_scope
from content_write_values import text_value as _text, visibility_value as _visibility
from content_visibility import media_visibility, rehearsal_visibility
from errors import NotFoundError, ValidationError
from extensions import db
from models import DirectUploadSession, Media, MultipartUploadSession, Rehearsal, Song
from policy_service import confirm_public_expansion, upload_policy_enabled
from voc_validation import validate_rehearsal_type


REHEARSAL_FIELDS = {
    'title', 'date', 'start_date', 'end_date', 'time', 'location', 'latitude', 'longitude',
    'memo', 'color', 'visibility', 'song_ids', 'visibility_confirm', 'rehearsal_type',
}


def _scope_for_write(band_id, *, lock=False, lock_actor=False):
    user = current_app_user(required=True)
    return active_member_write_scope(band_id, user, lock=lock, lock_actor=lock_actor)


def _run_rehearsal_write(operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


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


def _songs_or_404(band_id, song_ids):
    if not isinstance(song_ids, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in song_ids):
        raise ValidationError('song_ids must be an array of integer IDs.')
    wanted = set(song_ids)
    songs = Song.query.filter(Song.band_id == band_id, Song.id.in_(wanted)).all() if wanted else []
    if len(songs) != len(wanted):
        raise NotFoundError('One or more songs are unavailable for this rehearsal.')
    return sorted(songs, key=lambda item: item.id)


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


def create_rehearsal(*, band_id, data, prepare_response):
    """Create and audit an event; response preparation precedes commit."""
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
        return prepare_response(scope, rehearsal)
    return _run_rehearsal_write(operation)


def update_rehearsal(*, band_id, rehearsal_id, data, prepare_response):
    """Edit event associations and confirm effective media expansion atomically."""
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
        audit('content.rehearsal.updated', user, band, rehearsal, {'fields': sorted(set(data) & REHEARSAL_FIELDS)})
        return prepare_response(scope, rehearsal)
    return _run_rehearsal_write(operation)


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


def delete_rehearsal(*, band_id, rehearsal_id, prepare_response):
    """Delete the parent and terminal upload history in one transaction."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        rehearsal = _owned_or_404(Rehearsal, rehearsal_id, band.id, 'Rehearsal not found', lock=True)
        if not content_capabilities(scope, rehearsal)['can_delete']:
            raise ValidationError('Only an active verified band owner or platform administrator may delete content.', 403)
        _assert_rehearsal_delete_safe(rehearsal)
        from deletion_service import cleanup_terminal_parent_upload_sessions
        if not cleanup_terminal_parent_upload_sessions('rehearsal', rehearsal):
            raise ValidationError('Upload history is not in a safe terminal state.', 409)
        audit('content.rehearsal.deleted', user, band, rehearsal)
        db.session.delete(rehearsal)
        return prepare_response(rehearsal_id)

    return _run_rehearsal_write(operation)
