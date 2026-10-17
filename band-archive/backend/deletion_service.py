"""Authorization, key validation, and durable deletion task helpers.

The queue stores only server-derived object keys.  Callers never supply an
object key, filename, URL, or storage account identifier.
"""
from datetime import datetime, timezone
import re
import uuid

from sqlalchemy import or_, and_

from extensions import db
from band_service import ACTIVE
from models import (Announcement, AppUser, Band, BandMembership, Comment, GalleryImage,
                    Media, Member, PersonalLog, Rehearsal, R2DeletionTask, Song,
                    SongSuggestion)

RESOURCE_MODELS = {
    'media': Media,
    'personal_log': PersonalLog,
    'gallery': GalleryImage,
    'song': Song,
    'rehearsal': Rehearsal,
    'member': Member,
    'comment': Comment,
    'suggestion': SongSuggestion,
    'announcement': Announcement,
}
FILE_RESOURCES = {'media', 'personal_log', 'gallery'}
ERROR_CODES = {'none', 'storage_unavailable', 'key_conflict', 'invalid_key', 'unknown'}
_BASENAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$')


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def actor_is_eligible(user):
    return bool(user and user.status == ACTIVE and user.email_verified is True)


def load_current_actor(user_id, *, lock=True):
    query = AppUser.query.filter_by(id=user_id)
    if lock and db.engine.dialect.name == 'postgresql':
        query = query.with_for_update()
    return query.populate_existing().one_or_none()


def lock_band_and_membership(band_id, user_id, *, require_member):
    band_query = Band.query.filter(Band.id == band_id, Band.deleted_at.is_(None))
    membership_query = BandMembership.query.filter_by(band_id=band_id, user_id=user_id, status=ACTIVE)
    if db.engine.dialect.name == 'postgresql':
        band_query = band_query.with_for_update()
        membership_query = membership_query.with_for_update()
    band = band_query.populate_existing().one_or_none()
    if band is None:
        return None, None
    membership = membership_query.populate_existing().one_or_none()
    if require_member and membership is None:
        return band, None
    return band, membership


def _validated_basename(value):
    if not isinstance(value, str) or not _BASENAME.fullmatch(value) or value in {'.', '..'}:
        raise ValueError('invalid_key')
    return value


def _canonical_or_legacy(band, category, canonical, legacy):
    if canonical:
        expected = f'bands/{band.id}/{category}/'
        legacy_prefix = f'{category}/'
        if canonical.startswith(expected):
            basename = canonical[len(expected):]
        elif band.is_legacy_import and canonical.startswith(legacy_prefix):
            basename = canonical[len(legacy_prefix):]
        else:
            raise ValueError('invalid_key')
        if '/' in basename or _validated_basename(basename) != basename:
            raise ValueError('invalid_key')
        return canonical
    if not legacy:
        return None
    basename = _validated_basename(legacy)
    if band.is_legacy_import:
        return f'{category}/{basename}'
    raise ValueError('invalid_key')


def _object_keys(band, resource_type, target):
    if resource_type == 'gallery':
        keys = [_canonical_or_legacy(band, 'gallery', target.object_key, target.filename)]
    elif resource_type == 'personal_log':
        keys = [
            _canonical_or_legacy(band, 'personal_logs', target.object_key, target.filename),
            _canonical_or_legacy(band, 'personal_logs', target.audio_object_key, target.audio_filename),
        ]
    else:
        keys = [
            _canonical_or_legacy(band, 'media', target.object_key, target.filename),
            _canonical_or_legacy(band, 'media', target.audio_object_key, target.audio_filename),
            _canonical_or_legacy(band, 'media', target.video_720_object_key, target.video_720_filename),
        ]
    keys = list(dict.fromkeys(key for key in keys if key))
    if any(not isinstance(key, str) or len(key) > 500 for key in keys):
        raise ValueError('invalid_key')
    return keys


def validate_unshared_manifest(band_id, target, keys, resource_type=None):
    if not keys:
        return
    candidates = set(keys)
    # Exact canonical object-key references are checked globally, including
    # corrupt cross-band references. Queries are bounded by this target's
    # (at most three) manifest keys; unrelated rows are never materialized.
    for model, columns in (
        (Media, (Media.object_key, Media.audio_object_key, Media.video_720_object_key)),
        (PersonalLog, (PersonalLog.object_key, PersonalLog.audio_object_key)),
        (GalleryImage, (GalleryImage.object_key,)),
        (Song, (Song.sheet_music_object_key,)),
    ):
        for column in columns:
            query = model.query.filter(column.in_(candidates))
            if model is type(target):
                query = query.filter(or_(model.id != target.id, model.band_id != band_id))
            if query.first() is not None:
                raise ValueError('key_conflict')

    # Historical schemas can store bare filenames rather than canonical keys.
    # Resolve only aliases for the candidate paths, across all bands; this is
    # essential for the unscoped legacy object namespaces.
    basename_by_prefix = {}
    for key in keys:
        pieces = key.split('/')
        if len(pieces) == 2 and pieces[0] in {'media', 'personal_logs', 'gallery'}:
            basename_by_prefix.setdefault(pieces[0], set()).add(pieces[1])
    aliases = (
        ('media', Media, (Media.filename, Media.audio_filename, Media.video_720_filename)),
        ('personal_logs', PersonalLog, (PersonalLog.filename, PersonalLog.audio_filename)),
        ('gallery', GalleryImage, (GalleryImage.filename,)),
    )
    for prefix, model, columns in aliases:
        basenames = basename_by_prefix.get(prefix)
        if not basenames:
            continue
        for column in columns:
            query = model.query.filter(column.in_(basenames))
            if model is type(target):
                query = query.filter(or_(model.id != target.id, model.band_id != band_id))
            if query.first() is not None:
                raise ValueError('key_conflict')
    sheet_basenames = basename_by_prefix.get('media')
    if sheet_basenames and Song.query.filter(Song.sheet_music.in_(sheet_basenames)).first() is not None:
        raise ValueError('key_conflict')

    # Live upload capabilities are reservations. Completed sessions are
    # represented by their content row and do not permanently pin a key.
    from models import DirectUploadSession, MultipartUploadSession, PersonalLogMultipartUploadSession
    now = utcnow().replace(tzinfo=None)
    for model in (DirectUploadSession, MultipartUploadSession, PersonalLogMultipartUploadSession):
        query = model.query.filter(model.object_key.in_(candidates),
                                   model.status.in_(('initiated', 'completing', 'expiring', 'aborting')))
        if hasattr(model, 'expires_at'):
            query = query.filter(or_(model.status.in_(('expiring', 'aborting')), model.expires_at > now))
        if query.first() is not None:
            raise ValueError('key_conflict')


def lock_target(resource_type, band_id, resource_id):
    model = RESOURCE_MODELS.get(resource_type)
    if model is None:
        return None
    query = model.query.filter_by(id=resource_id, band_id=band_id)
    if db.engine.dialect.name == 'postgresql':
        query = query.with_for_update()
    return query.populate_existing().one_or_none()


def block_for_processing(resource_type, target):
    if resource_type not in {'media', 'personal_log'}:
        return False
    return getattr(target, 'transcoding_status', None) in {'queued', 'processing'}


def cleanup_terminal_parent_upload_sessions(resource_type, target):
    """Remove only terminal, result-free session rows before deleting a parent.

    Session FKs are intentionally restrictive. Live or cleanup-in-progress
    reservations block parent deletion; terminal rows with an attached result
    are treated as inconsistent and fail closed.
    """
    from models import (DirectUploadSession, MultipartUploadSession,
                        PersonalLogMultipartUploadSession)

    if resource_type == 'song':
        rows = (
            DirectUploadSession.query.filter_by(song_id=target.id).limit(1001).all()
            + MultipartUploadSession.query.filter_by(song_id=target.id).limit(1001).all()
        )
    elif resource_type == 'rehearsal':
        rows = (
            DirectUploadSession.query.filter_by(rehearsal_id=target.id).limit(1001).all()
            + MultipartUploadSession.query.filter_by(rehearsal_id=target.id).limit(1001).all()
        )
    elif resource_type == 'member':
        rows = (
            DirectUploadSession.query.filter_by(member_id=target.id).limit(1001).all()
            + PersonalLogMultipartUploadSession.query.filter_by(member_id=target.id).limit(1001).all()
        )
    else:
        return True

    if len(rows) > 1000:
        return False

    for row in rows:
        if row.band_id != target.band_id:
            return False
        if any(getattr(row, name, None) is not None for name in
               ('media_id', 'personal_log_id', 'result_gallery_image_id')):
            return False
        terminal = {'completed', 'expired', 'failed', 'aborted'}
        if row.status not in terminal:
            return False
    for row in rows:
        db.session.delete(row)
    return True


def create_or_get_task(band, actor, resource_type, target):
    if resource_type not in RESOURCE_MODELS:
        raise ValueError('unknown_resource')
    existing = R2DeletionTask.query.filter_by(
        band_id=band.id, resource_type=resource_type, resource_id=target.id,
    ).with_for_update().one_or_none() if db.engine.dialect.name == 'postgresql' else R2DeletionTask.query.filter_by(
        band_id=band.id, resource_type=resource_type, resource_id=target.id,
    ).one_or_none()
    if existing:
        return existing, False
    keys = _object_keys(band, resource_type, target) if resource_type in FILE_RESOURCES else []
    if resource_type in FILE_RESOURCES:
        validate_unshared_manifest(band.id, target, keys, resource_type)
    task = R2DeletionTask(
        id=str(uuid.uuid4()), band_id=band.id, resource_type=resource_type,
        resource_id=target.id, status='queued', object_keys=keys,
        attempt_count=0, available_at=utcnow(), error_code='none', actor_user_id=actor.id,
    )
    db.session.add(task)
    return task, True


def task_public_dict(task):
    return {
        'task_id': task.id, 'status': task.status, 'resource_type': task.resource_type,
        'resource_id': task.resource_id, 'attempts': task.attempt_count,
        'error_code': None if task.error_code == 'none' else task.error_code,
        'retry_after_seconds': 5 if task.status in {'queued', 'processing', 'retry_wait'} else None,
        'created_at': task.created_at.isoformat() if task.created_at else None,
        'updated_at': task.updated_at.isoformat() if task.updated_at else None,
        'completed_at': task.completed_at.isoformat() if task.completed_at else None,
    }


def is_pending_deletion(resource_type, resource_id, band_id):
    task = R2DeletionTask.query.filter_by(resource_type=resource_type, resource_id=resource_id,
                                          band_id=band_id).filter(
        R2DeletionTask.status != 'completed'
    ).first()
    return task is not None
