"""Reserve uploads using the caller's already-validated HTTP inputs.

The caller retains initial parsing, filename preparation, HTTP authentication,
response construction and direct presigning. These request-context use cases
retain the existing actor lookups: HTTP entry, active membership and policy.
Quota accounting performs DB reads and R2 expiry cleanup under the use case's
write locks. Reservation and policy audit persist together; cleanup failure
records its retry marker only after rollback. Multipart R2 creation is not
rolled back by DB failure, and no compensation is introduced here.
"""

from sqlalchemy import func

from auth_service import current_app_user
from direct_upload_service import (
    DirectUploadCleanupPending, create_direct_session,
    expire_direct_sessions_for_band, persist_direct_cleanup_pending,
)
from errors import ValidationError
from extensions import db
from models import (Media, PersonalLog, GalleryImage, MultipartUploadSession,
                    PersonalLogMultipartUploadSession, DirectUploadSession)
from multipart_upload_service import MEDIA_TARGET, PERSONAL_LOG_TARGET, create_session
from policy_service import validate_upload_assertion, record_upload_assertion
from upload_authorization_service import (
    active_upload_membership, validate_media_upload_target,
    validate_personal_log_upload_target,
)
from upload_transaction import run_upload_write
from validators import detect_file_type, guess_content_type


def _policy_session(factory, membership, assertion, *args, **kwargs):
    session, capability = factory(*args, **kwargs)
    record_upload_assertion(session, membership, assertion)
    return session, capability


def _reservation_write(operation):
    """Abort an initiate if expiry cleanup cannot retain its quota lock."""
    try:
        return run_upload_write(operation)
    except DirectUploadCleanupPending as pending:
        # run_upload_write already rolled back the accounting transaction. Persist the
        # retry marker only afterward, then fail closed instead of initiating a
        # new reservation from a lock that was no longer held.
        persist_direct_cleanup_pending(pending.session_id)
        raise ValidationError('An expired upload is still being cleaned up. Please retry shortly.', status_code=503)


def _used_and_reserved_bytes(band_id):
    """Logical object usage plus every still-live upload reservation."""
    # This runs inside run_upload_write after the SQLite immediate transaction (or
    # PostgreSQL parent row locks) is acquired. Do not commit expiry cleanup
    # here: releasing that transaction between quota accounting and session
    # creation lets concurrent initiates oversubscribe the same band.
    expire_direct_sessions_for_band(band_id, commit=False)
    used = sum((
        db.session.query(func.coalesce(func.sum(Media.file_size), 0)).filter_by(band_id=band_id).scalar() or 0,
        db.session.query(func.coalesce(func.sum(PersonalLog.file_size), 0)).filter_by(band_id=band_id).scalar() or 0,
        db.session.query(func.coalesce(func.sum(GalleryImage.file_size), 0)).filter_by(band_id=band_id).scalar() or 0,
    ))
    reserved = sum((
        db.session.query(func.coalesce(func.sum(MultipartUploadSession.declared_bytes), 0)).filter(
            MultipartUploadSession.band_id == band_id,
            MultipartUploadSession.status.in_(('initiated', 'completing', 'aborting')),
        ).scalar() or 0,
        db.session.query(func.coalesce(func.sum(PersonalLogMultipartUploadSession.declared_bytes), 0)).filter(
            PersonalLogMultipartUploadSession.band_id == band_id,
            PersonalLogMultipartUploadSession.status.in_(('initiated', 'completing', 'aborting')),
        ).scalar() or 0,
        db.session.query(func.coalesce(func.sum(DirectUploadSession.declared_bytes), 0)).filter(
            DirectUploadSession.band_id == band_id,
            DirectUploadSession.status.in_(('initiated', 'completing', 'expiring')),
        ).scalar() or 0,
    ))
    return int(used), int(reserved)


def reserve_direct_upload(band_id, data, *, filename, secure_name, declared_bytes, content_type, has_member):
    """Reserve a verified-shape media or personal-log direct upload."""
    def operation():
        band, membership = active_upload_membership(band_id, lock=True)
        assertion = validate_upload_assertion(data, current_app_user(required=True))
        used, reserved = _used_and_reserved_bytes(band.id)
        if used + reserved + declared_bytes > band.storage_quota_bytes:
            raise ValidationError('Band storage quota exceeded.', status_code=409)
        common = {
            'band_id': band.id, 'created_by_membership_id': membership.id,
            'original_filename': filename, 'content_type': content_type or guess_content_type(filename),
            'declared_bytes': declared_bytes, 'status': 'initiated', 'commit': False,
        }
        if has_member:
            if detect_file_type(filename) not in ('audio', 'video'):
                raise ValidationError('Personal logs support audio and video files only.')
            title = data.get('title', '').strip() if isinstance(data.get('title'), str) else ''
            if len(title or filename) > 200:
                raise ValidationError('title must be 200 characters or less')
            return _policy_session(create_direct_session, membership, assertion,
                kind='personal_log', object_key=f'bands/{band.id}/personal_logs/{secure_name}',
                member_id=validate_personal_log_upload_target(band.id, data.get('member_id')),
                title=title or filename, **common,
            )
        song_id, rehearsal_id = validate_media_upload_target(band.id, data)
        return _policy_session(create_direct_session, membership, assertion,
            kind='media', object_key=f'bands/{band.id}/media/{secure_name}',
            song_id=song_id, rehearsal_id=rehearsal_id, **common,
        )

    return _reservation_write(operation)


def reserve_gallery_upload(band_id, data, *, filename, secure_name, declared_bytes, content_type):
    """Reserve a verified-shape gallery direct upload."""
    def operation():
        band, membership = active_upload_membership(band_id, lock=True)
        assertion = validate_upload_assertion(data, current_app_user(required=True))
        used, reserved = _used_and_reserved_bytes(band.id)
        if used + reserved + declared_bytes > band.storage_quota_bytes:
            raise ValidationError('Band storage quota exceeded.', status_code=409)
        return _policy_session(create_direct_session, membership, assertion,
            band_id=band.id, created_by_membership_id=membership.id, kind='gallery',
            object_key=f'bands/{band.id}/gallery/{secure_name}', original_filename=filename,
            content_type=content_type or guess_content_type(filename), declared_bytes=declared_bytes,
            status='initiated', commit=False,
        )

    return _reservation_write(operation)


def reserve_multipart_upload(band_id, data, *, filename, secure_name, declared_bytes, content_type, has_member):
    """Reserve a verified-shape media or personal-log multipart upload."""
    def operation():
        band, membership = active_upload_membership(band_id, lock=True)
        assertion = validate_upload_assertion(data, current_app_user(required=True))
        used, reserved = _used_and_reserved_bytes(band.id)
        if used + reserved + declared_bytes > band.storage_quota_bytes:
            raise ValidationError('Band storage quota exceeded.', status_code=409)
        common = {
            'original_filename': filename, 'filename': secure_name,
            'content_type': content_type or guess_content_type(filename), 'declared_bytes': declared_bytes,
            'object_key': f'bands/{band.id}/{{prefix}}/{secure_name}', 'commit': False,
            'band_id': band.id, 'created_by_membership_id': membership.id, 'is_legacy': False,
        }
        if has_member:
            title = data.get('title', '').strip() if isinstance(data.get('title'), str) else ''
            if len(title or filename) > 200:
                raise ValidationError('title must be 200 characters or less')
            member_id = validate_personal_log_upload_target(band.id, data.get('member_id'))
            common['object_key'] = common['object_key'].format(prefix='personal_logs')
            return _policy_session(create_session, membership, assertion, PERSONAL_LOG_TARGET,
                                      member_id=member_id, title=title or filename, **common)
        song_id, rehearsal_id = validate_media_upload_target(band.id, data)
        common['object_key'] = common['object_key'].format(prefix='media')
        return _policy_session(create_session, membership, assertion, MEDIA_TARGET,
                                  song_id=song_id, rehearsal_id=rehearsal_id, **common)

    return _reservation_write(operation)
