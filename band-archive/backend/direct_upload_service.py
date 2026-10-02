"""Durable capability and completion state for v2 direct-to-R2 uploads."""

from datetime import datetime, timezone
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from errors import ValidationError
from models import DirectUploadSession, Media, PersonalLog


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


def expire_direct_session_if_needed(session):
    if session.status != 'initiated' or expires_at_utc_naive(session) > now():
        return False
    DirectUploadSession.query.filter_by(id=session.id, status='initiated').update({
        'status': 'expired', 'completion_started_at': None,
    })
    db.session.commit()
    return True


def expire_direct_sessions_for_band(band_id=None):
    """Release only expired PUT reservations before calculating a band quota."""
    query = DirectUploadSession.query.filter(
        DirectUploadSession.status == 'initiated',
        DirectUploadSession.expires_at <= now(),
    )
    if band_id is not None:
        query = query.filter(DirectUploadSession.band_id == band_id)
    expired = query.update({'status': 'expired', 'completion_started_at': None})
    if expired:
        db.session.flush()
    return expired


def recover_direct_sessions():
    """Repair a process-interrupted local completion without touching R2.

    A direct PUT is already durable before the API completion claim, so unlike
    multipart recovery this must never delete or recreate an object.  An
    existing materialized row proves completion; otherwise an interrupted
    claim is safely made retryable again.
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
    db.session.commit()


def completed_item(session):
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
    values['media_id' if session.kind == 'media' else 'personal_log_id'] = item.id
    DirectUploadSession.query.filter_by(id=session.id, status='completing').update(values)
    db.session.commit()


def complete_direct_session(session, object_size, create_item):
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
