"""Atomic SongSuggestion mutations and promotion into a Song."""

from sqlalchemy import text

from auth_service import current_app_user
from band_access import content_capabilities
from band_service import MANAGER_ROLES, audit
from comment_rules import _owned_or_404
from content_write_scope import active_member_write_scope
from content_write_values import (
    external_link_value as _external_link,
    text_value as _text,
    visibility_value as _visibility,
)
from errors import ValidationError
from extensions import db
from models import Song, SongSuggestion
from policy_service import confirm_public_expansion
from validators import validate_external_http_url


SUGGESTION_FIELDS = {'title', 'artist', 'link', 'memo', 'visibility', 'visibility_confirm'}


def _scope_for_write(band_id, *, lock=False, lock_actor=False):
    user = current_app_user(required=True)
    return active_member_write_scope(band_id, user, lock=lock, lock_actor=lock_actor)


def _run_suggestion_write(operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


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


def create_song_suggestion(*, band_id, data, prepare_response):
    """Create and audit a suggestion; response preparation precedes commit."""
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
        return prepare_response(scope, suggestion)
    return _run_suggestion_write(operation)


def update_song_suggestion(*, band_id, suggestion_id, data, prepare_response):
    """Update an authored suggestion and its audit in one transaction."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        suggestion = _owned_or_404(SongSuggestion, suggestion_id, band.id, 'Suggestion not found', lock=True)
        if not _can_change_suggestion(suggestion, membership):
            raise ValidationError('You may only edit your own suggestion.', 403)
        if band.default_visibility == 'PUBLIC' and suggestion.visibility == 'MEMBERS' and data.get('visibility') == 'PUBLIC':
            confirm_public_expansion(data, user, band, suggestion)
        _apply_suggestion(suggestion, data)
        audit('content.suggestion.updated', user, band, suggestion,
              {'fields': sorted(set(data) & SUGGESTION_FIELDS)})
        return prepare_response(scope, suggestion)
    return _run_suggestion_write(operation)


def delete_song_suggestion(*, band_id, suggestion_id, prepare_response):
    """Delete only with current server capability; response errors roll back."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        suggestion = _owned_or_404(SongSuggestion, suggestion_id, band.id, 'Suggestion not found', lock=True)
        if not content_capabilities(scope, suggestion)['can_delete']:
            raise ValidationError('Only an active verified band owner or platform administrator may delete content.', 403)
        audit('content.suggestion.deleted', user, band, suggestion)
        db.session.delete(suggestion)
        return prepare_response(suggestion_id)
    return _run_suggestion_write(operation)


def proceed_song_suggestion(*, band_id, suggestion_id, actor, visibility, prepare_response):
    """Commit one promotion; prepare its HTTP response before committing.

    ``actor`` is the server-authenticated AppUser, possibly expired by auth's
    separate commit. ``prepare_response`` belongs to the route and must finish
    serialization and JSON creation before this function commits. Its errors
    roll back the business transaction along with database errors.
    """
    try:
        # Do not access an expired actor attribute before SQLite's writer lock.
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        scope, band, membership, actor = active_member_write_scope(
            band_id, actor, lock=True, lock_actor=True,
        )
        if membership.role not in MANAGER_ROLES:
            raise ValidationError('Insufficient band role.', 403)
        suggestion = _owned_or_404(
            SongSuggestion, suggestion_id, band.id, 'Suggestion not found', lock=True,
        )
        if not content_capabilities(scope, suggestion)['can_delete']:
            raise ValidationError(
                'Only an active verified band owner or platform administrator may delete content.', 403,
            )
        if visibility is None:
            selected_visibility = suggestion.visibility or band.default_visibility
        else:
            if visibility not in {'PUBLIC', 'MEMBERS'}:
                raise ValidationError('visibility must be PUBLIC or MEMBERS.')
            selected_visibility = visibility
        if suggestion.link is None:
            raise ValidationError('link is required.')
        link = validate_external_http_url(suggestion.link, 'link', 500)
        song = Song(
            band_id=band.id, created_by_membership_id=membership.id,
            visibility=selected_visibility, title=suggestion.title, artist=suggestion.artist,
            link=link, memo=suggestion.memo, status='Practice',
        )
        db.session.add(song)
        db.session.flush()
        audit('content.suggestion.proceeded', actor, band, song, {
            'suggestion_id': suggestion.id, 'visibility': song.visibility,
        })
        db.session.delete(suggestion)
        response = prepare_response(scope, song)
        db.session.commit()
        return response
    except Exception:
        db.session.rollback()
        raise
