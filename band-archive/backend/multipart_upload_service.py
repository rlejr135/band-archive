"""Shared durable R2 multipart-session workflow for upload targets.

The media and personal-log schemas remain separate for compatibility.  Their
session state machine, capability boundary, and R2 completion behavior are
intentionally implemented once here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from errors import NotFoundError, ValidationError
from models import (Media, MultipartUploadPart, MultipartUploadSession, PersonalLog,
                    PersonalLogMultipartUploadPart, PersonalLogMultipartUploadSession)
from storage import storage


MULTIPART_PART_SIZE = 16 * 1024 * 1024
MAX_MULTIPART_PARTS = 10_000


@dataclass(frozen=True)
class MultipartTargetSpec:
    kind: str
    session_model: type
    part_model: type
    item_model: type
    key_prefix: str
    result_key: str
    item_id_field: str
    target_fields: tuple[str, ...]


MEDIA_TARGET = MultipartTargetSpec(
    kind='media', session_model=MultipartUploadSession, part_model=MultipartUploadPart,
    item_model=Media, key_prefix='media', result_key='media', item_id_field='media_id',
    target_fields=('song_id', 'rehearsal_id'),
)
PERSONAL_LOG_TARGET = MultipartTargetSpec(
    kind='personal_log', session_model=PersonalLogMultipartUploadSession,
    part_model=PersonalLogMultipartUploadPart, item_model=PersonalLog,
    key_prefix='personal_logs', result_key='personal_log', item_id_field='personal_log_id',
    target_fields=('member_id',),
)
MULTIPART_TARGETS = (MEDIA_TARGET, PERSONAL_LOG_TARGET)


def now():
    """Use naive UTC because SQLite strips timezone information."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def spec_for_kind(kind):
    for spec in MULTIPART_TARGETS:
        if spec.kind == kind:
            return spec
    raise ValueError(f'Unknown multipart target: {kind}')


def spec_for_session(session):
    for spec in MULTIPART_TARGETS:
        if isinstance(session, spec.session_model):
            return spec
    raise TypeError(f'Unsupported multipart session: {type(session).__name__}')


def expires_at_utc_naive(session):
    expires_at = session.expires_at
    if expires_at and expires_at.tzinfo is not None:
        return expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    return expires_at


def expire_session_if_needed(session):
    if session.status not in ('initiated', 'completing') or expires_at_utc_naive(session) > now():
        return False
    try:
        storage.abort_multipart_upload(session.object_key, session.r2_upload_id)
    except Exception:
        current_app.logger.exception('Failed to abort expired multipart upload %s', session.id)
    session.status = 'expired'
    session.completion_started_at = None
    db.session.commit()
    return True


def get_session_or_404(session_id):
    for spec in MULTIPART_TARGETS:
        session = db.session.get(spec.session_model, session_id)
        if session:
            expire_session_if_needed(session)
            return session
    raise NotFoundError('Upload session not found')


def recover_sessions():
    """Reset interrupted completions and expire stale R2 upload IDs."""
    for spec in MULTIPART_TARGETS:
        for session in spec.session_model.query.filter_by(status='completing').all():
            if expires_at_utc_naive(session) > now():
                session.status = 'initiated'
                session.completion_started_at = None
        db.session.commit()
        for session in spec.session_model.query.filter(
                spec.session_model.status.in_(('initiated', 'completing'))).all():
            expire_session_if_needed(session)


def require_capability(session, token):
    if not token or not session.capability_token_hash:
        raise ValidationError('A valid upload capability is required.', status_code=403)
    if not check_password_hash(session.capability_token_hash, token):
        raise ValidationError('A valid upload capability is required.', status_code=403)


def create_session(spec, *, original_filename, content_type, declared_bytes, **target_values):
    filename = target_values.pop('filename')
    object_key = f'{spec.key_prefix}/{filename}'
    try:
        r2_upload_id = storage.create_multipart_upload(object_key, content_type)
    except Exception as exc:
        raise ValidationError('Multipart upload could not be initiated.') from exc
    capability_token = secrets.token_urlsafe(32)
    session = spec.session_model(
        r2_upload_id=r2_upload_id,
        object_key=object_key,
        original_filename=original_filename,
        content_type=content_type,
        declared_bytes=declared_bytes,
        capability_token_hash=generate_password_hash(capability_token),
        **target_values,
    )
    db.session.add(session)
    db.session.commit()
    return session, capability_token


def require_active_session(session_id, capability_token):
    session = get_session_or_404(session_id)
    require_capability(session, capability_token)
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    return session


def part_payload(part):
    payload = {
        'session_id': part.session_id,
        'part_number': part.part_number,
        'status': 'acknowledged' if part.acknowledged_at else 'issued',
    }
    if part.acknowledged_at:
        payload.update({
            'etag': part.etag,
            'bytes': part.uploaded_bytes,
            'checksum': part.checksum,
            'acknowledged_at': part.acknowledged_at.isoformat(),
        })
    return payload


def issue_part(session, part_number):
    spec = spec_for_session(session)
    part = spec.part_model.query.filter_by(session_id=session.id, part_number=part_number).first()
    if part and part.acknowledged_at:
        return part, False
    if not part:
        part = spec.part_model(session_id=session.id, part_number=part_number)
        db.session.add(part)
        db.session.commit()
    try:
        upload_url = storage.generate_upload_part_url(session.object_key, session.r2_upload_id, part_number)
    except Exception as exc:
        raise ValidationError('Part upload URL could not be created.') from exc
    return part, upload_url


def acknowledge_part(session, part_number, etag, uploaded_bytes, checksum):
    spec = spec_for_session(session)
    part = spec.part_model.query.filter_by(session_id=session.id, part_number=part_number).first()
    if not part:
        raise ValidationError('part_number has not been issued.', status_code=409)
    if part.acknowledged_at:
        if part.etag == etag and part.uploaded_bytes == uploaded_bytes and part.checksum == checksum:
            return part
        raise ValidationError('Part acknowledgement conflicts with the stored result.', status_code=409)
    part.etag = etag
    part.uploaded_bytes = uploaded_bytes
    part.checksum = checksum
    part.acknowledged_at = now()
    db.session.commit()
    return part


def acknowledged_completion_parts(session):
    parts = list(session.parts)
    if not parts:
        raise ValidationError('At least one acknowledged part is required.', status_code=409)
    if any(not part.acknowledged_at for part in parts):
        raise ValidationError('All issued parts must be acknowledged before completion.', status_code=409)
    if sum(part.uploaded_bytes for part in parts) != session.declared_bytes:
        raise ValidationError('Acknowledged part bytes do not match declared_bytes.')
    return [
        {'PartNumber': part.part_number, 'ETag': part.etag}
        for part in sorted(parts, key=lambda item: item.part_number)
    ]


def find_completed_item(session):
    spec = spec_for_session(session)
    item_id = getattr(session, spec.item_id_field)
    if item_id:
        return db.session.get(spec.item_model, item_id)
    filters = {
        field: getattr(session, field)
        for field in spec.target_fields
    }
    filters['filename'] = session.object_key.rsplit('/', 1)[1]
    return spec.item_model.query.filter_by(**filters).order_by(spec.item_model.id.desc()).first()


def mark_session_completed(session, item):
    spec = spec_for_session(session)
    session.status = 'completed'
    session.completion_started_at = None
    session.completed_at = now()
    setattr(session, spec.item_id_field, item.id)
    db.session.commit()


def completion_payload(session, item):
    spec = spec_for_session(session)
    return {'session_id': session.id, 'status': 'completed', spec.result_key: item.to_dict() if item else None}


def session_payload(session):
    spec = spec_for_session(session)
    target = {field: getattr(session, field) for field in spec.target_fields}
    target['kind'] = spec.kind
    if spec.kind == 'personal_log':
        target['title'] = session.title
    item = find_completed_item(session) if session.status == 'completed' else None
    return {
        'session_id': session.id,
        'status': session.status,
        'target': target,
        'declared_bytes': session.declared_bytes,
        'part_size': MULTIPART_PART_SIZE,
        'expires_at': session.expires_at.isoformat() if session.expires_at else None,
        'completed_at': session.completed_at.isoformat() if session.completed_at else None,
        'result': {spec.result_key: item.to_dict()} if item else None,
        'parts': [part_payload(part) for part in session.parts],
    }


def complete_session(session, object_size, create_item):
    """Finalize R2 and create the target record. Returns ``(item, status)``."""
    spec = spec_for_session(session)
    if session.status == 'completed':
        return find_completed_item(session), 200
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    parts = acknowledged_completion_parts(session)
    claimed = spec.session_model.query.filter_by(id=session.id, status='initiated').update({
        'status': 'completing', 'completion_started_at': now(),
    })
    db.session.commit()
    if not claimed:
        raise ValidationError('Upload session is already being completed.', status_code=409)

    session = db.session.get(spec.session_model, session.id)
    try:
        item = find_completed_item(session)
        if item:
            mark_session_completed(session, item)
            return item, 200
        try:
            storage.complete_multipart_upload(session.object_key, session.r2_upload_id, parts)
        except Exception as complete_error:
            # R2 can accept completion before a network timeout reaches us.
            try:
                if object_size(session.object_key) != session.declared_bytes:
                    raise complete_error
            except ValidationError:
                raise complete_error
        actual_size = object_size(session.object_key)
        if actual_size != session.declared_bytes:
            raise ValidationError('Uploaded object size does not match declared_bytes.')
        item = create_item(session, actual_size)
        mark_session_completed(session, item)
    except ValidationError:
        db.session.rollback()
        session = db.session.get(spec.session_model, session.id)
        if session and session.status == 'completing':
            session.status = 'failed'
            session.completion_started_at = None
            db.session.commit()
        raise
    except Exception:
        db.session.rollback()
        session = db.session.get(spec.session_model, session.id)
        if session and session.status == 'completing':
            # Transport errors are retryable; acknowledgements remain durable.
            session.status = 'initiated'
            session.completion_started_at = None
            db.session.commit()
        raise
    return item, 201


def abort_session(session):
    if session.status in ('aborted', 'completed', 'expired'):
        return session.status
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    try:
        storage.abort_multipart_upload(session.object_key, session.r2_upload_id)
    except Exception as exc:
        raise ValidationError('Multipart upload could not be aborted.') from exc
    session.status = 'aborted'
    db.session.commit()
    return session.status
