"""Public archive queries and narrowly scoped visibility writes."""

from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.orm import selectinload

from band_access import MEMBERS, PUBLIC, BandReadScope, MembershipAudience, RequestAudience
from band_content_predicates import _creator_is_scoped_sql, _not_pending_delete
from band_service import audit
from content_queries import (
    _media_pending_deletes_sql, _media_playable_sql, _song_graph_sql,
)
from content_restrictions import restriction_predicate
from content_visibility import graph_is_scoped, media_visibility, song_visibility
from deletion_service import actor_is_eligible, load_current_actor, lock_band_and_membership
from errors import NotFoundError, ValidationError
from extensions import db
from media_sources import select_media_source_keys
from models import Band, Media, Rehearsal, Song, rehearsal_songs
from policy_service import confirm_public_expansion


def public_song_query():
    # The ordinary visibility path and the opt-in path share tenant and
    # restriction checks. The exception applies only to this projection.
    return Song.query.join(Band, Band.id == Song.band_id).filter(
        Band.deleted_at.is_(None),
        Band.default_visibility.in_((PUBLIC, MEMBERS)),
        Song.visibility.in_((PUBLIC, MEMBERS)),
        or_(Song.public_override.is_(True), and_(
            Band.default_visibility == PUBLIC, Song.visibility == PUBLIC,
        )),
        *_song_graph_sql(_QueryScope(Song.band_id)),
        _creator_is_scoped_sql(Song, _QueryScope(Song.band_id)),
        restriction_predicate(Song),
        _not_pending_delete(Song, 'song'),
    )


class _QueryScope:
    """Supply the correlated tenant column to the existing SQL predicates."""

    def __init__(self, band_id):
        self.band_id = band_id


def _direct_rehearsal_allowed_sql(*, public_only):
    policies = (PUBLIC,) if public_only else (PUBLIC, MEMBERS)
    return or_(Media.rehearsal_id.is_(None), exists().where(and_(
        Rehearsal.id == Media.rehearsal_id,
        Rehearsal.band_id == Media.band_id,
        Rehearsal.visibility.in_(policies),
    )))


def _linked_rehearsals_allowed_sql(*, public_only):
    policies = (PUBLIC,) if public_only else (PUBLIC, MEMBERS)
    return ~select(1).select_from(rehearsal_songs).outerjoin(
        Rehearsal, Rehearsal.id == rehearsal_songs.c.rehearsal_id,
    ).where(and_(
        rehearsal_songs.c.song_id == Media.song_id,
        or_(Rehearsal.id.is_(None), Rehearsal.band_id != Media.band_id,
            Rehearsal.visibility.is_(None), Rehearsal.visibility.notin_(policies)),
    )).exists()


def public_media_query():
    eligible_songs = public_song_query().with_entities(Song.id).statement
    return Media.query.filter(
        Media.song_id.in_(eligible_songs),
        exists().where(and_(Song.id == Media.song_id, Song.band_id == Media.band_id)),
        Media.visibility == PUBLIC,
        _direct_rehearsal_allowed_sql(public_only=True),
        _linked_rehearsals_allowed_sql(public_only=True),
        _creator_is_scoped_sql(Media, _QueryScope(Media.band_id)),
        *_media_pending_deletes_sql(), _media_playable_sql(), restriction_predicate(Media),
    )


def can_change_archive_visibility(scope):
    audience = getattr(scope, 'audience', None)
    if audience is None:
        return False
    return bool(audience.authenticated and audience.account_active and audience.email_verified
                and (scope.is_member or audience.is_platform_admin))


def song_archive_flags(scope, song):
    override = bool(song.public_override)
    return {
        'public_override': override,
        'archive_visibility': PUBLIC if override else song_visibility(scope, song),
        'can_change_public_override': can_change_archive_visibility(scope),
    }


def _playable(media):
    sources = select_media_source_keys(media, 'media')
    return bool((media.file_type == 'audio' and sources['original'])
                or (media.file_type == 'video' and (sources['video_720'] or sources['original'])))


def media_archive_flags(scope, media):
    reason = None
    if media.visibility != PUBLIC:
        reason = 'media'
    elif any(rehearsal.visibility != PUBLIC for rehearsal in media.song.rehearsals):
        reason = 'rehearsal'
    elif media.rehearsal is not None and media.rehearsal.visibility != PUBLIC:
        reason = 'rehearsal'
    elif not media.song.public_override and scope.band_visibility not in (None, PUBLIC):
        reason = 'band'
    elif not media.song.public_override and media.song.visibility != PUBLIC:
        reason = 'song'
    elif not _playable(media):
        reason = 'unavailable'
    return {
        'archive_visibility': MEMBERS if reason else PUBLIC,
        'public_block_reason': reason,
        'can_change_visibility': can_change_archive_visibility(scope),
    }


def _run_visibility_write(band_id, actor_id, operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        band, membership = lock_band_and_membership(band_id, actor_id, require_member=False)
        if band is None:
            raise NotFoundError('Band not found')
        actor = load_current_actor(actor_id)
        if not actor_is_eligible(actor):
            raise ValidationError('An active, verified account is required.', 403)
        if membership is None and actor.is_platform_admin is not True:
            raise ValidationError('An active band membership is required.', 403)
        audience = RequestAudience(actor.id, actor.supabase_user_id, True, True, actor.is_platform_admin is True)
        member = MembershipAudience(
            membership.id, band.id, membership.role, membership.status,
            actor.supabase_user_id, actor.display_name, actor.email,
        ) if membership is not None else None
        scope = BandReadScope(band.id, band.slug, audience, member, band.default_visibility)
        result = operation(scope, band, actor)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def _lock_item(query):
    if db.engine.dialect.name == 'postgresql':
        query = query.with_for_update()
    item = query.populate_existing().one_or_none()
    if item is None:
        raise NotFoundError('Resource not found')
    return item


def _check_confirmation(data, field, public):
    if set(data) - {field, 'rights_confirmed', 'visibility_confirm'}:
        raise ValidationError('Unsupported visibility fields.')
    if 'rights_confirmed' in data and type(data['rights_confirmed']) is not bool:
        raise ValidationError('rights_confirmed must be a boolean.')
    if public and data.get('rights_confirmed') is not True:
        raise ValidationError('Public sharing rights must be confirmed.')


def change_song_public_override(band_id, song_id, actor_id, data):
    enabled = data.get('public_override')
    if type(enabled) is not bool:
        raise ValidationError('public_override must be a boolean.')
    _check_confirmation(data, 'public_override', enabled)

    def operation(scope, band, actor):
        song = _lock_item(Song.query.filter(
            Song.id == song_id, Song.band_id == band.id,
            *_song_graph_sql(scope), _creator_is_scoped_sql(Song, scope),
            restriction_predicate(Song, band_id=band.id), _not_pending_delete(Song, 'song'),
        ))
        if song.public_override != enabled:
            if enabled:
                confirm_public_expansion(data, actor, band, song)
            song.public_override = enabled
            audit('content.song.public_override.changed', actor, band, song, {
                'public_override': enabled, 'rights_confirmed': enabled,
            })
        return {'id': song.id, 'effective_visibility': song_visibility(scope, song),
                **song_archive_flags(scope, song)}

    return _run_visibility_write(band_id, actor_id, operation)


def change_media_visibility(band_id, media_id, actor_id, data):
    visibility = data.get('visibility')
    if visibility not in (PUBLIC, MEMBERS):
        raise ValidationError('visibility must be PUBLIC or MEMBERS.')
    _check_confirmation(data, 'visibility', visibility == PUBLIC)

    def operation(scope, band, actor):
        media = _lock_item(Media.query.options(
            selectinload(Media.rehearsal), selectinload(Media.song).selectinload(Song.rehearsals),
        ).filter(
            Media.id == media_id, Media.band_id == band.id,
            _creator_is_scoped_sql(Media, scope),
            _direct_rehearsal_allowed_sql(public_only=False),
            _linked_rehearsals_allowed_sql(public_only=False),
            *_media_pending_deletes_sql(), restriction_predicate(Media, band_id=band.id),
        ))
        if not graph_is_scoped(scope, media):
            raise NotFoundError('Resource not found')
        if media.visibility != visibility:
            if visibility == PUBLIC:
                confirm_public_expansion(data, actor, band, media)
            media.visibility = visibility
            audit('content.media.visibility.changed', actor, band, media, {
                'visibility': visibility, 'rights_confirmed': visibility == PUBLIC,
            })
        return {'id': media.id, 'visibility': visibility,
                'effective_visibility': media_visibility(scope, media),
                **media_archive_flags(scope, media)}

    return _run_visibility_write(band_id, actor_id, operation)
