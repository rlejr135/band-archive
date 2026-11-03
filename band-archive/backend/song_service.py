"""Atomic creation and editing of a band's Song repertoire entries."""

from sqlalchemy import text

from auth_service import current_app_user
from band_service import audit
from comment_rules import _owned_or_404
from content_write_rules import can_change_authored_content as _can_change
from content_write_scope import active_member_write_scope
from content_write_values import text_value as _text, visibility_value as _visibility
from content_visibility import song_visibility
from errors import ValidationError
from extensions import db
from models import Song
from policy_service import confirm_public_expansion


SONG_FIELDS = {'title', 'artist', 'status', 'chords', 'link', 'memo', 'genre', 'difficulty', 'visibility', 'visibility_confirm'}


def _scope_for_write(band_id, *, lock=False, lock_actor=False):
    user = current_app_user(required=True)
    return active_member_write_scope(band_id, user, lock=lock, lock_actor=lock_actor)


def _run_song_write(operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


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


def create_song(*, band_id, data, prepare_response):
    """Create and audit a song; response preparation precedes commit."""
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
        return prepare_response(scope, song)
    return _run_song_write(operation)


def update_song(*, band_id, song_id, data, prepare_response):
    """Edit a song and confirm effective public expansion in the same transaction."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        song = _owned_or_404(Song, song_id, band.id, 'Song not found', lock=True)
        if not _can_change(song, membership):
            raise ValidationError('You may only edit your own content.', 403)
        before_visibility = song_visibility(scope, song)
        _apply_song(song, data)
        if before_visibility == 'MEMBERS' and song_visibility(scope, song) == 'PUBLIC':
            confirm_public_expansion(data, user, band, song)
        audit('content.song.updated', user, band, song, {'fields': sorted(set(data) & SONG_FIELDS)})
        return prepare_response(scope, song)
    return _run_song_write(operation)
