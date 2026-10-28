"""Materialize verified upload objects after the caller's reauthorization.

Callers retain capability checks, object verification, tenant authorization and
completion state transitions. These context-dependent callbacks read ORM state;
they do not establish authorization on their own. Media and personal-log saves
commit the record before waking video work, while gallery creation only adds
and flushes. Session completion is a separate step, not one shared transaction.
"""

from flask import current_app

from extensions import db
from media_processing import (create_media, create_personal_log, save_media_and_start,
                              save_personal_log_and_start)
from models import Band, GalleryImage
from multipart_upload_service import PERSONAL_LOG_TARGET, spec_for_session
from policy_service import upload_session_visibility
from upload_record_service import create_uploaded_record


MAX_VIDEO_BYTES = 1024 * 1024 * 1024


def materialize_multipart_upload(session, actual_size):
    """Materialize only the verified, tenant-scoped object at completion."""
    filename = session.object_key.rsplit('/', 1)[1]
    band = db.session.get(Band, session.band_id)
    spec = spec_for_session(session)
    fields = {
        'band_id': session.band_id,
        'created_by_membership_id': session.created_by_membership_id,
        'visibility': upload_session_visibility(session, band.default_visibility),
        'object_key': session.object_key,
    }
    if spec is PERSONAL_LOG_TARGET:
        return create_uploaded_record(
            filename=filename, original_filename=session.original_filename, actual_size=actual_size,
            max_video_bytes=MAX_VIDEO_BYTES, create_record=create_personal_log,
            save_record=save_personal_log_and_start, app=current_app._get_current_object(),
            member_id=session.member_id, title=session.title, **fields,
        )
    return create_uploaded_record(
        filename=filename, original_filename=session.original_filename, actual_size=actual_size,
        max_video_bytes=MAX_VIDEO_BYTES, create_record=create_media,
        save_record=save_media_and_start, app=current_app._get_current_object(),
        song_id=session.song_id, rehearsal_id=session.rehearsal_id, **fields,
    )


def materialize_direct_upload(session, actual_size):
    """Materialize a verified direct object only within its original band."""
    band = db.session.get(Band, session.band_id)
    filename = session.object_key.rsplit('/', 1)[1]
    fields = {
        'band_id': session.band_id,
        'created_by_membership_id': session.created_by_membership_id,
        'visibility': upload_session_visibility(session, band.default_visibility),
        'object_key': session.object_key,
    }
    if session.kind == 'personal_log':
        return create_uploaded_record(
            filename=filename, original_filename=session.original_filename, actual_size=actual_size,
            max_video_bytes=MAX_VIDEO_BYTES, create_record=create_personal_log,
            save_record=save_personal_log_and_start, app=current_app._get_current_object(),
            member_id=session.member_id, title=session.title, **fields,
        )
    return create_uploaded_record(
        filename=filename, original_filename=session.original_filename, actual_size=actual_size,
        max_video_bytes=MAX_VIDEO_BYTES, create_record=create_media,
        save_record=save_media_and_start, app=current_app._get_current_object(),
        song_id=session.song_id, rehearsal_id=session.rehearsal_id, **fields,
    )


def materialize_direct_gallery_upload(session, actual_size):
    """Materialize a verified gallery object in the session's original band."""
    band = db.session.get(Band, session.band_id)
    image = GalleryImage(
        band_id=session.band_id, created_by_membership_id=session.created_by_membership_id,
        visibility=upload_session_visibility(session, band.default_visibility), filename=session.object_key.rsplit('/', 1)[1],
        original_filename=session.original_filename, object_key=session.object_key,
        file_size=actual_size,
    )
    db.session.add(image); db.session.flush()
    return image
