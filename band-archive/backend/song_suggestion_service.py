"""Atomic promotion of one SongSuggestion into a Song."""

from sqlalchemy import text

from band_access import content_capabilities
from band_service import MANAGER_ROLES, audit
from comment_rules import _owned_or_404
from content_write_scope import active_member_write_scope
from errors import ValidationError
from extensions import db
from models import Song, SongSuggestion
from validators import validate_external_http_url


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
