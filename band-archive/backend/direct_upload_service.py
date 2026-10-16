"""Durable capability and completion state for v2 direct-to-R2 uploads."""

from datetime import datetime, timezone
import secrets

from botocore.exceptions import ClientError
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from errors import ValidationError
from models import DirectUploadSession, GalleryImage, Media, PersonalLog
from storage import storage


class DirectUploadCleanupPending(Exception):
    """An expired reservation could not be safely released in this write."""

    def __init__(self, session_id):
        self.session_id = session_id


def now():
    """Use naive UTC because SQLite does not preserve timezone information."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_direct_session(*, commit=True, **fields):
    """Persist a direct-upload quota reservation and return its one-time secret."""
    capability_token = secrets.token_urlsafe(32)
    session = DirectUploadSession(
        capability_token_hash=generate_password_hash(capability_token), **fields,
    )
    db.session.add(session)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return session, capability_token


def require_direct_capability(session, token):
    if not token or not check_password_hash(session.capability_token_hash, token):
        raise ValidationError('A valid upload capability is required.', status_code=403)


def expires_at_utc_naive(session):
    expires_at = session.expires_at
    if expires_at and expires_at.tzinfo is not None:
        return expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    return expires_at


def expire_direct_session_if_needed(session, *, commit=True):
    if session.status == 'expiring':
        return False
    if session.status != 'initiated' or expires_at_utc_naive(session) > now():
        return False
    claimed = DirectUploadSession.query.filter_by(id=session.id, status='initiated').update({
        'status': 'expiring', 'completion_started_at': now(),
    })
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    if not claimed:
        return False
    recovered = recover_expiring_session(db.session.get(DirectUploadSession, session.id), commit=commit)
    if not recovered and not commit:
        # The caller must roll back its quota transaction before recording the
        # retry marker in a separate transaction. In particular, never commit
        # here: that would release SQLite's immediate lock and let this caller
        # continue from stale quota accounting.
        raise DirectUploadCleanupPending(session.id)
    return recovered


def expire_direct_sessions_for_band(band_id=None, *, commit=True):
    """Clean expired direct PUTs before releasing their quota reservations."""
    query = DirectUploadSession.query.filter(
        DirectUploadSession.status == 'initiated',
        DirectUploadSession.expires_at <= now(),
    )
    if band_id is not None:
        query = query.filter(DirectUploadSession.band_id == band_id)
    sessions = query.all()
    return sum(expire_direct_session_if_needed(session, commit=commit) for session in sessions)


def persist_direct_cleanup_pending(session_id):
    """Durably retain a failed cleanup reservation after the caller rolls back."""
    DirectUploadSession.query.filter_by(id=session_id, status='initiated').update({
        'status': 'expiring', 'completion_started_at': now(),
    })
    db.session.commit()


def _missing_object(error):
    """Treat R2/S3's idempotent missing-object responses as cleaned up."""
    if not isinstance(error, ClientError):
        return False
    code = str(error.response.get('Error', {}).get('Code', '')).lower()
    http_status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
    return code in {'nosuchkey', 'nosuchobject', 'notfound', '404'} or http_status == 404


def recover_expiring_session(session, *, commit=True):
    """Retry cleanup of a session-owned direct object before releasing quota."""
    if session.status != 'expiring':
        return session.status == 'expired'
    try:
        storage.delete(session.object_key)
    except Exception as exc:
        if not _missing_object(exc):
            return False
    DirectUploadSession.query.filter_by(id=session.id, status='expiring').update({
        'status': 'expired', 'completion_started_at': None,
    })
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return True


def recover_direct_sessions():
    """Repair interrupted completion and expiration claims.

    A direct PUT is already durable before the API completion claim, so unlike
    multipart completion recovery this never recreates an object. An existing
    materialized row proves completion; otherwise the claim is retryable.
    Expiring rows are the exception: their session-owned object is deleted
    before quota can be released.
    """
    for session in DirectUploadSession.query.filter_by(status='completing').all():
        item = completed_item(session)
        if item:
            mark_direct_session_completed(session, item)
        else:
            DirectUploadSession.query.filter_by(id=session.id, status='completing').update({
                'status': 'initiated', 'completion_started_at': None,
            })
    expire_direct_sessions_for_band(None)
    for session in DirectUploadSession.query.filter_by(status='expiring').all():
        recover_expiring_session(session)
    db.session.commit()


def completed_item(session):
    if session.kind == 'gallery':
        if session.result_gallery_image_id:
            return GalleryImage.query.filter_by(id=session.result_gallery_image_id, band_id=session.band_id).one_or_none()
        return GalleryImage.query.filter_by(
            band_id=session.band_id, object_key=session.object_key,
        ).order_by(GalleryImage.id.desc()).first()
    if session.kind == 'media':
        if session.media_id:
            return Media.query.filter_by(id=session.media_id, band_id=session.band_id).one_or_none()
        return Media.query.filter_by(
            band_id=session.band_id, song_id=session.song_id,
            rehearsal_id=session.rehearsal_id,
            filename=session.object_key.rsplit('/', 1)[1],
        ).order_by(Media.id.desc()).first()
    if session.personal_log_id:
        return PersonalLog.query.filter_by(id=session.personal_log_id, band_id=session.band_id).one_or_none()
    return PersonalLog.query.filter_by(
        band_id=session.band_id, member_id=session.member_id,
        filename=session.object_key.rsplit('/', 1)[1],
    ).order_by(PersonalLog.id.desc()).first()


def mark_direct_session_completed(session, item):
    values = {'status': 'completed', 'completion_started_at': None, 'completed_at': now()}
    values[{'media': 'media_id', 'personal_log': 'personal_log_id', 'gallery': 'result_gallery_image_id'}[session.kind]] = item.id
    DirectUploadSession.query.filter_by(id=session.id, status='completing').update(values)
    db.session.commit()


def complete_direct_session(session, object_size, create_item, before_materialize=None):
    """Verify a reserved object and materialize exactly one tenant-scoped row."""
    if session.status == 'completed':
        return completed_item(session), 200
    if session.status == 'completing':
        item = completed_item(session)
        if item:
            mark_direct_session_completed(session, item)
            return item, 200
        raise ValidationError('Upload session is already being completed.', status_code=409)
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)

    claimed = DirectUploadSession.query.filter_by(id=session.id, status='initiated').update({
        'status': 'completing', 'completion_started_at': now(),
    })
    db.session.commit()
    if not claimed:
        raise ValidationError('Upload session is already being completed.', status_code=409)

    session = db.session.get(DirectUploadSession, session.id)
    try:
        item = completed_item(session)
        if item:
            mark_direct_session_completed(session, item)
            return item, 200
        actual_size = object_size(session.object_key)
        if actual_size != session.declared_bytes:
            raise ValidationError('Uploaded object size does not match declared_bytes.')
        # The initial capability check and completing claim are separate
        # commits. Recheck tenant authority after external object verification
        # and keep its band/membership locks through record materialization.
        if before_materialize is not None:
            before_materialize(session)
        item = create_item(session, actual_size)
        mark_direct_session_completed(session, item)
        return item, 201
    except Exception:
        db.session.rollback()
        DirectUploadSession.query.filter_by(id=session.id, status='completing').update({
            'status': 'initiated', 'completion_started_at': None,
        })
        db.session.commit()
        raise
