"""Case-based access restrictions, independent of audience and deletion.

The table contains opaque case identifiers and bounded reason codes, never
complaints, personal evidence, file keys, or signed URLs. Existing signed URLs
remain usable until their expiry; this module stops new API reads and signing.
"""
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, exists, inspect, literal, or_, select, true, union_all
from sqlalchemy.orm import aliased
from flask import has_request_context, request

from errors import NotFoundError, ValidationError
from extensions import db
from models import (Announcement, AppUser, AuditEvent, Band, Comment, ContentRestriction,
                    GalleryImage, Media, Member, PersonalLog, Rehearsal, Song,
                    SongSuggestion, rehearsal_songs)


RESOURCE_MODELS = {
    'song': Song, 'rehearsal': Rehearsal, 'member': Member, 'media': Media,
    'personal_log': PersonalLog, 'gallery': GalleryImage,
    'announcement': Announcement, 'suggestion': SongSuggestion, 'comment': Comment,
}
REASON_CODES = ('copyright', 'privacy', 'portrait', 'other_rights')


def _request_cache():
    if not has_request_context():
        return None
    current = request._get_current_object()
    cache = getattr(current, '_content_restriction_cache', None)
    if cache is None:
        cache = {'records': {}, 'bands': {}}
        current._content_restriction_cache = cache
    return cache


def _has_active_restrictions():
    cache = _request_cache()
    if cache is None:
        return True  # CLI/worker checks never inherit an HTTP snapshot.
    if 'active' not in cache:
        cache['active'] = db.session.query(ContentRestriction.id).filter_by(state='active').first() is not None
    return cache['active']


def clear_restriction_cache():
    if has_request_context():
        request._get_current_object()._content_restriction_cache = None


def _band_has_active_restrictions(band_id):
    if not _has_active_restrictions():
        return False
    cache = _request_cache()
    if cache is None:
        return True
    if band_id not in cache['bands']:
        cache['bands'][band_id] = db.session.query(ContentRestriction.id).filter_by(
            state='active', band_id=band_id,
        ).first() is not None
    return cache['bands'][band_id]


def prime_unrestricted_records(records):
    """Cache rows only after an SQL restriction predicate already allowed them."""
    cache = _request_cache()
    if cache is not None:
        for record in records:
            if type(record) in RESOURCE_MODELS.values():
                cache['records'][(record.band_id, type(record).__name__, record.id)] = False


def _own_allowed(entity, kind):
    return ~exists().where(and_(
        ContentRestriction.band_id == entity.band_id,
        ContentRestriction.resource_type == kind,
        ContentRestriction.resource_id == entity.id,
        ContentRestriction.state == 'active',
    ))


def restriction_predicate(entity, *, band_id=None):
    """SQL access predicate; all case/parent checks retain the item's band."""
    if not _has_active_restrictions():
        return true()
    if band_id is not None and not _band_has_active_restrictions(band_id):
        return true()
    inspected = inspect(entity)
    model = getattr(inspected, 'mapper', inspected).class_
    kind = next((name for name, candidate in RESOURCE_MODELS.items() if candidate is model), None)
    if kind is None:
        return true()
    conditions = [_own_allowed(entity, kind)]
    if model is Media:
        song = aliased(Song)
        rehearsal = aliased(Rehearsal)
        conditions.append(~exists().where(and_(
            song.id == entity.song_id, song.band_id == entity.band_id,
            ~_own_allowed(song, 'song'),
        )))
        conditions.append(~exists().where(and_(
            rehearsal.id == entity.rehearsal_id, rehearsal.band_id == entity.band_id,
            ~_own_allowed(rehearsal, 'rehearsal'),
        )))
        conditions.append(~exists().where(and_(
            rehearsal_songs.c.song_id == entity.song_id,
            rehearsal_songs.c.rehearsal_id == rehearsal.id,
            rehearsal.band_id == entity.band_id,
            ~_own_allowed(rehearsal, 'rehearsal'),
        )))
    elif model is PersonalLog:
        member = aliased(Member)
        conditions.append(~exists().where(and_(
            member.id == entity.member_id, member.band_id == entity.band_id,
            ~_own_allowed(member, 'member'),
        )))
    elif model is Comment:
        media, log = aliased(Media), aliased(PersonalLog)
        conditions.extend((
            ~exists().where(and_(media.id == entity.media_id,
                                media.band_id == entity.band_id, ~restriction_predicate(media))),
            ~exists().where(and_(log.id == entity.personal_log_id,
                                log.band_id == entity.band_id, ~restriction_predicate(log))),
        ))
        # UNION (not UNION ALL) also terminates malformed cyclic parent graphs.
        parent = aliased(Comment)
        ancestors = select(parent.id, parent.band_id, parent.parent_id).where(
            parent.id == entity.parent_id, parent.band_id == entity.band_id,
        ).correlate(entity).cte(recursive=True)
        ancestor_parent = aliased(Comment)
        ancestors = ancestors.union(select(
            ancestor_parent.id, ancestor_parent.band_id, ancestor_parent.parent_id,
        ).join(ancestors, and_(ancestor_parent.id == ancestors.c.parent_id,
                              ancestor_parent.band_id == ancestors.c.band_id)))
        conditions.append(~exists().where(and_(
            ContentRestriction.band_id == entity.band_id,
            ContentRestriction.resource_type == 'comment',
            ContentRestriction.resource_id.in_(select(ancestors.c.id)),
            ContentRestriction.state == 'active',
        )))
    return and_(*conditions)


def is_restricted(record):
    if record is None or getattr(record, 'id', None) is None:
        return False
    model = type(record)
    if model not in RESOURCE_MODELS.values():
        return False
    if not _band_has_active_restrictions(record.band_id):
        return False
    cache = _request_cache()
    key = (record.band_id, model.__name__, record.id)
    if cache is not None and key in cache['records']:
        return cache['records'][key]
    result = db.session.query(model.id).filter(
        model.id == record.id, model.band_id == record.band_id,
        ~restriction_predicate(model),
    ).first() is not None
    if cache is not None:
        cache['records'][key] = result
    return result


def require_unrestricted(record):
    """Conceal restricted targets equally from visitors, owners, and admins."""
    if is_restricted(record):
        raise NotFoundError('Resource not found')
    return record


def require_unrestricted_key(key):
    """Stop signing a shared R2 key if any restricted record references it."""
    if not _has_active_restrictions():
        return
    cache = _request_cache()
    if cache is not None:
        if 'blocked_keys' not in cache:
            source_columns = (
                (Media, (Media.object_key, Media.audio_object_key, Media.video_720_object_key,
                         'media/' + Media.filename, 'media/' + Media.audio_filename, Media.video_720_filename)),
                (PersonalLog, (PersonalLog.object_key, PersonalLog.audio_object_key,
                               'personal_logs/' + PersonalLog.filename, 'personal_logs/' + PersonalLog.audio_filename)),
                (GalleryImage, (GalleryImage.object_key, 'gallery/' + GalleryImage.filename)),
                (Song, (Song.sheet_music_object_key, 'media/' + Song.sheet_music)),
            )
            selections = [select(*(columns + (literal(None),) * (6 - len(columns)))).where(
                ~restriction_predicate(model)) for model, columns in source_columns]
            rows = db.session.execute(union_all(*selections)).all()
            cache['blocked_keys'] = {value for row in rows for value in row if value}
        if key in cache['blocked_keys']:
            raise NotFoundError('Resource not found')
        return
    candidates = (
        (Media, or_(Media.object_key == key, Media.audio_object_key == key,
                    Media.video_720_object_key == key, 'media/' + Media.filename == key,
                    'media/' + Media.audio_filename == key, Media.video_720_filename == key)),
        (PersonalLog, or_(PersonalLog.object_key == key, PersonalLog.audio_object_key == key,
                          'personal_logs/' + PersonalLog.filename == key,
                          'personal_logs/' + PersonalLog.audio_filename == key)),
        (GalleryImage, or_(GalleryImage.object_key == key, 'gallery/' + GalleryImage.filename == key)),
        (Song, or_(Song.sheet_music_object_key == key, 'media/' + Song.sheet_music == key)),
    )
    blocked = db.session.query(or_(*(
        exists().where(and_(key_match, ~restriction_predicate(model)))
        for model, key_match in candidates
    ))).scalar()
    if blocked:
        raise NotFoundError('Resource not found')


def _actor_or_error(actor_user_id):
    actor = AppUser.query.filter_by(id=actor_user_id).populate_existing().with_for_update().one_or_none()
    if (actor is None or actor.status != 'active' or actor.email_verified is not True
            or actor.is_platform_admin is not True):
        raise ValidationError('An active verified platform administrator is required.', 403)
    return actor


def set_restriction(*, band_id, resource_type, resource_id, case_ref, actor_user_id,
                    active, reason_code=None, apply=False):
    """Review or atomically apply one case; never contacts or deletes storage."""
    if resource_type not in RESOURCE_MODELS or type(resource_id) is not int or resource_id <= 0:
        raise ValidationError('Invalid restriction target.')
    try:
        case_ref = str(uuid.UUID(case_ref))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('case_ref must be an opaque UUID.')
    if active and reason_code not in REASON_CODES:
        raise ValidationError('Invalid restriction reason code.')
    # Consistent band-first lock also serializes concurrent upload completion.
    band = Band.query.filter_by(id=band_id, deleted_at=None).with_for_update().one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    actor = _actor_or_error(actor_user_id)
    model = RESOURCE_MODELS[resource_type]
    target = model.query.filter_by(id=resource_id, band_id=band.id).with_for_update().one_or_none()
    if target is None:
        raise NotFoundError('Resource not found')
    item = ContentRestriction.query.filter_by(
        band_id=band.id, resource_type=resource_type, resource_id=resource_id, case_ref=case_ref,
    ).with_for_update().one_or_none()
    if not active and item is None:
        raise NotFoundError('Restriction case not found')
    changed = item is None or item.state != ('active' if active else 'released')
    if active and item is not None and item.reason_code != reason_code:
        raise ValidationError('An existing case reason cannot be changed.')
    if apply and changed:
        now = datetime.now(timezone.utc)
        if item is None:
            item = ContentRestriction(
                band_id=band.id, resource_type=resource_type, resource_id=resource_id,
                case_ref=case_ref, reason_code=reason_code, created_by_user_id=actor.id, created_at=now,
            )
            db.session.add(item)
        item.state = 'active' if active else 'released'
        item.released_at = None if active else now
        item.released_by_user_id = None if active else actor.id
        db.session.flush()
        db.session.add(AuditEvent(
            band_id=band.id, actor_user_id=actor.id,
            event_type='content_restricted' if active else 'content_restriction_released',
            target_type=resource_type, target_id=str(resource_id),
            details_json=json.dumps({'case_ref': case_ref, 'reason_code': item.reason_code,
                                     'restriction_id': item.id}, sort_keys=True),
        ))
        db.session.flush()
        clear_restriction_cache()
    return {'band_id': band.id, 'resource_type': resource_type, 'resource_id': resource_id,
            'case_ref': case_ref, 'state': 'active' if active else 'released',
            'changed': changed, 'applied': bool(apply),
            'existing_signed_urls_may_remain': True}
