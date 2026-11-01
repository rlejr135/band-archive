"""Atomic owner-selected FeaturedMedia changes, without HTTP response creation.

These use cases retain the existing session and writer-lock ordering. They
commit flags and audit together, then return the compact action result. The
route creates JSON only after that commit; a response failure does not undo it.
"""

from sqlalchemy import text
from sqlalchemy.orm import joinedload

from auth_service import current_app_user
from band_access import BandReadScope, MembershipAudience, RequestAudience
from band_content_predicates import _not_pending_delete
from band_service import audit
from content_queries import _media_pending_deletes_sql, _media_playable_sql
from content_visibility import graph_is_scoped
from errors import NotFoundError, ValidationError
from extensions import db
from models import Media, Song


_VISIBILITIES = {'PUBLIC', 'MEMBERS'}


def _run_featured_write(operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def set_song_featured_media(*, band_id, song_id, media_id):
    """Select one playable candidate while preserving owner/admin authority."""
    def operation():
        from deletion_service import actor_is_eligible, load_current_actor, lock_band_and_membership
        from media_sources import select_media_source_keys
        from content_restrictions import require_unrestricted
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
        scope = BandReadScope(
            band_id=band.id, band_slug=band.slug, audience=audience, membership=member_audience,
            band_visibility=band.default_visibility,
        )

        song_query = Song.query.filter_by(id=song_id, band_id=band.id)
        if db.engine.dialect.name != 'sqlite':
            song_query = song_query.with_for_update()
        song = song_query.populate_existing().one_or_none()
        if song is None:
            raise NotFoundError('Resource not found')
        require_unrestricted(song)
        if song.visibility not in _VISIBILITIES:
            raise NotFoundError('Resource not found')

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

    return _run_featured_write(operation)


def clear_song_featured_media(*, band_id, song_id):
    """Clear the current selection without changing media or its files."""
    def operation():
        from deletion_service import actor_is_eligible, load_current_actor, lock_band_and_membership
        from content_restrictions import require_unrestricted

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

    return _run_featured_write(operation)
