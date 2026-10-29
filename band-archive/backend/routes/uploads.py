from functools import wraps

from flask import Blueprint, after_this_request, jsonify, request, current_app
from sqlalchemy import inspect

from auth_service import current_app_user
from extensions import db
from models import (Song, Rehearsal, Member, GalleryImage, MultipartUploadSession,
                    PersonalLogMultipartUploadSession, DirectUploadSession)
from direct_upload_service import (
    complete_direct_session, expire_direct_session_if_needed,
    recover_direct_sessions, require_direct_capability,
)
from legacy_scope import legacy_band_id, legacy_get_or_404, validate_legacy_graph
from errors import ValidationError, NotFoundError
from storage import storage
from media_processing import (create_media, create_personal_log, save_media_and_start,
                              save_personal_log_and_start)
from upload_record_service import create_uploaded_record
from upload_initiation_service import (
    reserve_direct_upload, reserve_gallery_upload, reserve_multipart_upload,
)
from upload_transaction import run_upload_write
from upload_authorization_service import (
    active_upload_membership, require_upload_session_owner,
    reauthorize_upload_session_owner,
)
from upload_materialization_service import (
    MAX_VIDEO_BYTES, materialize_multipart_upload, materialize_direct_upload,
    materialize_direct_gallery_upload,
)
from policy_service import upload_policy_enabled
from multipart_upload_service import (
    MAX_MULTIPART_PARTS, MULTIPART_PART_SIZE, MEDIA_TARGET, PERSONAL_LOG_TARGET,
    abort_session, acknowledge_part, complete_session, completion_payload, create_session,
    expire_session_if_needed,
    get_session_or_404, issue_part, part_payload, recover_sessions, require_capability,
    session_payload, spec_for_session,
)
from validators import (allowed_file, allowed_image_file, generate_secure_filename,
                        detect_file_type, guess_content_type, require_int)
from v2_upload_serializers import (
    serialize_direct_upload_completion, serialize_direct_gallery_completion,
    serialize_multipart_upload_completion,
)

uploads_bp = Blueprint('uploads', __name__)

UPLOAD_CAPABILITY_HEADER = 'X-Upload-Capability'


def _legacy_upload_band_id():
    """Resolve the only tenant v1 upload endpoints are allowed to mutate."""
    if upload_policy_enabled():
        raise ValidationError('Use the authenticated band upload flow.', 410,
                              code='legacy_v1_writes_disabled')
    return legacy_band_id()


def _v2_upload_write(view):
    """Authenticate a tenant upload write without changing v1 behavior."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        @after_this_request
        def add_headers(response):
            response.headers['Cache-Control'] = 'private, no-store'
            response.vary.add('Authorization')
            return response
        if not current_app.config.get('BAND_API_SCHEMA_READY', False):
            return jsonify(error='band_schema_unavailable', message='Band API schema is unavailable.'), 503
        current_app_user(required=True)
        return view(*args, **kwargs)
    return wrapped


def _v2_session_or_404(session_id, band_id, *, recover_aborting=True):
    session = get_session_or_404(
        session_id, band_id=band_id, recover_aborting=recover_aborting,
    )
    if session.is_legacy:
        raise NotFoundError('Upload session not found')
    return session


def _v2_direct_session_or_404(session_id, band_id):
    session = DirectUploadSession.query.filter_by(id=session_id, band_id=band_id).one_or_none()
    if session is None:
        raise NotFoundError('Upload session not found')
    expire_direct_session_if_needed(session)
    return db.session.get(DirectUploadSession, session.id)


def _v2_direct_completion_session(session_id, band_id):
    """Authorize the original creator before expiry cleanup can touch R2."""
    _, membership = active_upload_membership(band_id, lock=True)
    session = DirectUploadSession.query.filter_by(id=session_id, band_id=band_id).one_or_none()
    if session is None:
        raise NotFoundError('Upload session not found')
    require_upload_session_owner(band_id, session, membership)
    expire_direct_session_if_needed(session)
    return db.session.get(DirectUploadSession, session.id)


def _v2_multipart_completion_session(session_id, band_id):
    """Authorize the original creator before multipart recovery/expiry I/O."""
    _, membership = active_upload_membership(band_id, lock=True)
    session = None
    for model in (MultipartUploadSession, PersonalLogMultipartUploadSession):
        session = model.query.filter_by(id=session_id, band_id=band_id).one_or_none()
        if session is not None:
            break
    if session is None or session.is_legacy:
        raise NotFoundError('Upload session not found')
    require_upload_session_owner(band_id, session, membership)
    return _v2_session_or_404(session_id, band_id, recover_aborting=False)


def _legacy_session_or_404(session_id, *, recover_aborting=True):
    """Load an upload capability only after both tenant and parent are proven."""
    band_id = _legacy_upload_band_id()
    session = get_session_or_404(
        session_id, band_id=band_id, expire=False, recover_aborting=recover_aborting,
    )
    validate_legacy_graph(session, 'Upload session not found')
    expire_session_if_needed(session)
    return session


def _validate_media_target(song_id, rehearsal_id):
    legacy_band_id = _legacy_upload_band_id()
    song_id = require_int(song_id, 'song_id', minimum=1)
    song = legacy_get_or_404(Song, song_id, 'Song not found')
    if rehearsal_id is not None:
        rehearsal_id = require_int(rehearsal_id, 'rehearsal_id', minimum=1)
        legacy_get_or_404(Rehearsal, rehearsal_id, 'Rehearsal not found')
    return song_id, rehearsal_id


def _validate_personal_log_target(member_id):
    legacy_band_id = _legacy_upload_band_id()
    member_id = require_int(member_id, 'member_id', minimum=1)
    legacy_get_or_404(Member, member_id, 'Member not found')
    return member_id


def _object_size(key):
    try:
        size = storage.head(key).get('ContentLength')
    except Exception as exc:
        raise ValidationError('Uploaded object could not be verified.') from exc
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValidationError('Uploaded object has an invalid size.')
    return size


def _create_media_from_object(song_id, rehearsal_id, filename, original_filename, actual_size):
    return create_uploaded_record(
        filename=filename, original_filename=original_filename, actual_size=actual_size,
        max_video_bytes=MAX_VIDEO_BYTES, create_record=create_media,
        save_record=save_media_and_start, app=current_app._get_current_object(),
        song_id=song_id, rehearsal_id=rehearsal_id,
    )


def _create_personal_log_from_object(member_id, filename, original_filename, title, actual_size):
    return create_uploaded_record(
        filename=filename, original_filename=original_filename, actual_size=actual_size,
        max_video_bytes=MAX_VIDEO_BYTES, create_record=create_personal_log,
        save_record=save_personal_log_and_start, app=current_app._get_current_object(),
        member_id=member_id, title=title,
    )


def _verified_direct_upload_size(data, key_prefix, filename):
    actual_size = _object_size(f'{key_prefix}/{filename}')
    declared_size = data.get('file_size')
    if declared_size is not None and require_int(declared_size, 'file_size', minimum=0) != actual_size:
        raise ValidationError('Uploaded object size does not match file_size.')
    return actual_size


def recover_multipart_upload_sessions(app):
    """Recover interrupted completion attempts and expire old R2 upload IDs.

    This runs on process startup.  It does not expose or regenerate legacy
    capabilities, so an old session without a capability remains inaccessible.
    """
    with app.app_context():
        # The Phase 4 model is additive, but a process may intentionally boot
        # against a recorded older schema while the isolated runner is pending.
        # Avoid selecting future ORM columns in that compatibility interval.
        columns = {column['name'] for column in inspect(db.engine).get_columns('multipart_upload_session')}
        if 'band_id' not in columns:
            return
        recover_sessions()
        if inspect(db.engine).has_table('direct_upload_session'):
            recover_direct_sessions()


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/direct/initiate', methods=['POST'])
@_v2_upload_write
def v2_initiate_direct_upload(band_id):
    """Reserve a small direct upload before issuing its tenant-scoped PUT URL."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError('A JSON object is required.')
    filename = data.get('filename', '').strip() if isinstance(data.get('filename'), str) else ''
    if not filename or not allowed_file(filename):
        raise ValidationError('A supported filename is required.')
    declared_bytes = require_int(data.get('declared_bytes'), 'declared_bytes', minimum=1,
                                  maximum=MAX_VIDEO_BYTES)
    has_member = data.get('member_id') is not None
    has_song_target = data.get('song_id') is not None or data.get('rehearsal_id') is not None
    if has_member == has_song_target:
        raise ValidationError('Exactly one media or personal-log target is required.')
    content_type = data.get('content_type', '').strip() if isinstance(data.get('content_type'), str) else ''
    secure_name = generate_secure_filename(filename)

    session, capability_token = reserve_direct_upload(
        band_id, data, filename=filename, secure_name=secure_name,
        declared_bytes=declared_bytes, content_type=content_type, has_member=has_member,
    )
    return jsonify({
        'session_id': session.id, 'filename': secure_name,
        'upload_url': storage.generate_upload_url(session.object_key, content_type=session.content_type),
        'expires_at': session.expires_at.isoformat(),
        'upload_capability_token': capability_token, 'capability_header': UPLOAD_CAPABILITY_HEADER,
    }), 201


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/direct/<session_id>/complete', methods=['POST'])
@_v2_upload_write
def v2_complete_direct_upload(band_id, session_id):
    def operation():
        session = _v2_direct_completion_session(session_id, band_id)
        require_direct_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
        return complete_direct_session(
            session, _object_size, materialize_direct_upload,
            before_materialize=lambda completing: reauthorize_upload_session_owner(band_id, completing),
        )

    item, status = run_upload_write(operation)
    session = db.session.get(DirectUploadSession, session_id)
    return jsonify(serialize_direct_upload_completion(session, item)), status


@uploads_bp.route('/api/v2/bands/<int:band_id>/gallery/direct/initiate', methods=['POST'])
@_v2_upload_write
def v2_initiate_direct_gallery_upload(band_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError('A JSON object is required.')
    filename = data.get('filename', '').strip() if isinstance(data.get('filename'), str) else ''
    if not filename or not allowed_image_file(filename):
        raise ValidationError('A supported image filename is required.')
    declared_bytes = require_int(data.get('declared_bytes'), 'declared_bytes', minimum=1,
                                  maximum=MAX_VIDEO_BYTES)
    content_type = data.get('content_type', '').strip() if isinstance(data.get('content_type'), str) else ''
    secure_name = generate_secure_filename(filename)

    session, capability_token = reserve_gallery_upload(
        band_id, data, filename=filename, secure_name=secure_name,
        declared_bytes=declared_bytes, content_type=content_type,
    )
    return jsonify({
        'session_id': session.id, 'filename': secure_name,
        'upload_url': storage.generate_upload_url(session.object_key, content_type=session.content_type),
        'expires_at': session.expires_at.isoformat(),
        'upload_capability_token': capability_token, 'capability_header': UPLOAD_CAPABILITY_HEADER,
    }), 201


@uploads_bp.route('/api/v2/bands/<int:band_id>/gallery/direct/<session_id>/complete', methods=['POST'])
@_v2_upload_write
def v2_complete_direct_gallery_upload(band_id, session_id):
    def operation():
        session = _v2_direct_completion_session(session_id, band_id)
        if session.kind != 'gallery':
            raise NotFoundError('Upload session not found')
        require_direct_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
        return complete_direct_session(
            session, _object_size, materialize_direct_gallery_upload,
            before_materialize=lambda completing: reauthorize_upload_session_owner(band_id, completing),
        )

    item, status = run_upload_write(operation)
    session = db.session.get(DirectUploadSession, session_id)
    return jsonify(serialize_direct_gallery_completion(session, item)), status


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/multipart/initiate', methods=['POST'])
@_v2_upload_write
def v2_initiate_multipart_upload(band_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError('A JSON object is required.')
    filename = data.get('filename', '').strip() if isinstance(data.get('filename'), str) else ''
    if not filename or not allowed_file(filename) or detect_file_type(filename) != 'video':
        raise ValidationError('A supported video filename is required.')
    declared_bytes = require_int(data.get('declared_bytes'), 'declared_bytes', minimum=1, maximum=MAX_VIDEO_BYTES)
    has_member = data.get('member_id') is not None
    has_song_target = data.get('song_id') is not None or data.get('rehearsal_id') is not None
    if has_member == has_song_target:
        raise ValidationError('Exactly one media or personal-log target is required.')
    content_type = data.get('content_type', '').strip() if isinstance(data.get('content_type'), str) else ''
    secure_name = generate_secure_filename(filename)

    session, capability_token = reserve_multipart_upload(
        band_id, data, filename=filename, secure_name=secure_name,
        declared_bytes=declared_bytes, content_type=content_type, has_member=has_member,
    )
    return jsonify({
        'session_id': session.id, 'filename': secure_name, 'part_size': MULTIPART_PART_SIZE,
        'max_parts': MAX_MULTIPART_PARTS, 'expires_at': session.expires_at.isoformat(),
        'upload_capability_token': capability_token, 'capability_header': UPLOAD_CAPABILITY_HEADER,
    }), 201


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/multipart/<session_id>', methods=['GET'])
def v2_get_multipart_session(band_id, session_id):
    session = _v2_session_or_404(session_id, band_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    return jsonify(session_payload(session)), 200


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/multipart/<session_id>/parts', methods=['POST'])
def v2_presign_multipart_part(band_id, session_id):
    session = _v2_session_or_404(session_id, band_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError('A JSON object is required.')
    part, upload_url = issue_part(session, require_int(data.get('part_number'), 'part_number', minimum=1, maximum=MAX_MULTIPART_PARTS))
    if upload_url is False:
        return jsonify(part_payload(part)), 409
    payload = part_payload(part); payload.update({'upload_url': upload_url, 'status': 'issued'})
    return jsonify(payload), 200


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/multipart/<session_id>/parts/<int:part_number>/ack', methods=['POST'])
def v2_acknowledge_multipart_part(band_id, session_id, part_number):
    session = _v2_session_or_404(session_id, band_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError('A JSON object is required.')
    etag = data.get('etag'); size = data.get('bytes')
    if not isinstance(etag, str) or not etag.strip() or len(etag) > 500:
        raise ValidationError('A valid ETag is required.')
    checksum = data.get('checksum')
    if checksum is not None and (not isinstance(checksum, str) or not checksum.strip() or len(checksum) > 200):
        raise ValidationError('checksum must be a non-empty string of 200 characters or less.')
    part = acknowledge_part(session, require_int(part_number, 'part_number', minimum=1, maximum=MAX_MULTIPART_PARTS),
                            etag.strip(), require_int(size, 'bytes', minimum=1, maximum=MULTIPART_PART_SIZE),
                            checksum.strip() if checksum else None)
    return jsonify(part_payload(part)), 200


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/multipart/<session_id>/complete', methods=['POST'])
@_v2_upload_write
def v2_complete_multipart_upload(band_id, session_id):
    def operation():
        session = _v2_multipart_completion_session(session_id, band_id)
        require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
        return complete_session(
            session, _object_size, materialize_multipart_upload,
            before_materialize=lambda completing: reauthorize_upload_session_owner(band_id, completing),
        )
    item, status = run_upload_write(operation)
    session = _v2_session_or_404(session_id, band_id)
    return jsonify(serialize_multipart_upload_completion(session, item)), status


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/multipart/<session_id>/abort', methods=['POST'])
def v2_abort_multipart_upload(band_id, session_id):
    session = _v2_session_or_404(session_id, band_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    return jsonify({'session_id': session.id, 'status': abort_session(session)}), 200


@uploads_bp.route('/uploads/presign', methods=['POST'])
def presign():
    _legacy_upload_band_id()
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')
    filename = data.get('filename', '').strip()
    content_type = data.get('content_type', '').strip()
    upload_type = data.get('upload_type', '').strip()
    if not filename:
        raise ValidationError('filename is required')
    if upload_type not in ('media', 'gallery', 'personal_log'):
        raise ValidationError("upload_type must be 'media', 'gallery', or 'personal_log'")
    if upload_type == 'media' and not allowed_file(filename):
        raise ValidationError('File type not allowed')
    if upload_type == 'gallery' and not allowed_image_file(filename):
        raise ValidationError('Image files only. Allowed: png, jpg, jpeg, gif, webp')
    if upload_type == 'personal_log':
        if data.get('song_id') is not None or data.get('rehearsal_id') is not None:
            raise ValidationError('personal_log uploads cannot include song_id or rehearsal_id.')
        _validate_personal_log_target(data.get('member_id'))
        if detect_file_type(filename) not in ('audio', 'video'):
            raise ValidationError('Personal logs support audio and video files only.')
    if not content_type:
        content_type = guess_content_type(filename)
    secure_name = generate_secure_filename(filename)
    key_prefix = 'personal_logs' if upload_type == 'personal_log' else upload_type
    key = f'{key_prefix}/{secure_name}'
    return jsonify({
        'upload_url': storage.generate_upload_url(key, content_type=content_type),
        'key': key, 'filename': secure_name, 'content_type': content_type,
    }), 200


@uploads_bp.route('/uploads/complete/media', methods=['POST'])
def complete_media():
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')
    filename = data.get('filename', '').strip()
    original_filename = data.get('original_filename', '').strip()
    if not filename:
        raise ValidationError('filename is required')
    if not allowed_file(filename):
        raise ValidationError('File type not allowed')
    song_id, rehearsal_id = _validate_media_target(data.get('song_id'), data.get('rehearsal_id'))
    actual_size = _verified_direct_upload_size(data, 'media', filename)
    media = _create_media_from_object(song_id, rehearsal_id, filename, original_filename, actual_size)
    return jsonify(media.to_dict()), 201


@uploads_bp.route('/uploads/complete/personal-log', methods=['POST'])
def complete_personal_log():
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')
    if data.get('song_id') is not None or data.get('rehearsal_id') is not None:
        raise ValidationError('personal_log uploads cannot include song_id or rehearsal_id.')
    filename = data.get('filename', '').strip()
    original_filename = data.get('original_filename', '').strip()
    if not filename or not allowed_file(filename) or detect_file_type(filename) not in ('audio', 'video'):
        raise ValidationError('A supported audio or video filename is required.')
    member_id = _validate_personal_log_target(data.get('member_id'))
    title = data.get('title', '').strip() or original_filename or filename
    if len(title) > 200:
        raise ValidationError('title must be 200 characters or less')
    actual_size = _verified_direct_upload_size(data, 'personal_logs', filename)
    log = _create_personal_log_from_object(member_id, filename, original_filename, title, actual_size)
    return jsonify(log.to_dict()), 201


@uploads_bp.route('/uploads/complete/gallery', methods=['POST'])
def complete_gallery():
    legacy_band_id = _legacy_upload_band_id()
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')
    filename = data.get('filename', '').strip()
    original_filename = data.get('original_filename', '').strip()
    if not filename:
        raise ValidationError('filename is required')
    if not allowed_image_file(filename):
        raise ValidationError('Image files only.')
    actual_size = _object_size(f'gallery/{filename}')
    declared_size = data.get('file_size')
    if declared_size is not None and require_int(declared_size, 'file_size', minimum=0) != actual_size:
        raise ValidationError('Uploaded object size does not match file_size.')
    image = GalleryImage(
        band_id=legacy_band_id, filename=filename,
        original_filename=original_filename or None, file_size=actual_size,
    )
    db.session.add(image)
    db.session.commit()
    return jsonify(image.to_dict()), 201


@uploads_bp.route('/uploads/multipart/initiate', methods=['POST'])
def initiate_multipart_media():
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')
    filename = data.get('filename', '').strip()
    if not filename or not allowed_file(filename) or detect_file_type(filename) != 'video':
        raise ValidationError('A supported video filename is required.')
    declared_bytes = require_int(data.get('declared_bytes'), 'declared_bytes', minimum=1, maximum=MAX_VIDEO_BYTES)
    has_member = data.get('member_id') is not None
    has_song_target = data.get('song_id') is not None or data.get('rehearsal_id') is not None
    if has_member and has_song_target:
        raise ValidationError('member_id cannot be combined with song_id or rehearsal_id.')
    if not has_member and not has_song_target:
        raise ValidationError('A song or member upload target is required.')
    if has_member:
        member_id = _validate_personal_log_target(data.get('member_id'))
        title = data.get('title', '').strip() or filename
        if len(title) > 200:
            raise ValidationError('title must be 200 characters or less')
    else:
        song_id, rehearsal_id = _validate_media_target(data.get('song_id'), data.get('rehearsal_id'))
    content_type = data.get('content_type', '').strip() or guess_content_type(filename)
    secure_name = generate_secure_filename(filename)
    if has_member:
        session, capability_token = create_session(
            PERSONAL_LOG_TARGET, original_filename=filename, filename=secure_name,
            content_type=content_type, declared_bytes=declared_bytes,
            member_id=member_id, title=title, band_id=_legacy_upload_band_id(),
        )
    else:
        session, capability_token = create_session(
            MEDIA_TARGET, original_filename=filename, filename=secure_name,
            content_type=content_type, declared_bytes=declared_bytes,
            song_id=song_id, rehearsal_id=rehearsal_id, band_id=_legacy_upload_band_id(),
        )
    return jsonify({
        'session_id': session.id, 'filename': secure_name, 'part_size': MULTIPART_PART_SIZE,
        'max_parts': MAX_MULTIPART_PARTS, 'expires_at': session.expires_at.isoformat(),
        'upload_capability_token': capability_token,
        'capability_header': UPLOAD_CAPABILITY_HEADER,
    }), 201


@uploads_bp.route('/uploads/multipart/<session_id>/parts', methods=['POST'])
def presign_multipart_part(session_id):
    session = _legacy_session_or_404(session_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')
    part_number = require_int(data.get('part_number'), 'part_number', minimum=1, maximum=MAX_MULTIPART_PARTS)
    part, upload_url = issue_part(session, part_number)
    if upload_url is False:
        return jsonify(part_payload(part)), 409
    payload = part_payload(part)
    payload.update({'upload_url': upload_url, 'status': 'issued'})
    return jsonify(payload), 200


@uploads_bp.route('/uploads/multipart/<session_id>/parts/<int:part_number>/ack', methods=['POST'])
def acknowledge_multipart_part(session_id, part_number):
    session = _legacy_session_or_404(session_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    require_int(part_number, 'part_number', minimum=1, maximum=MAX_MULTIPART_PARTS)
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')
    etag = data.get('etag')
    if not isinstance(etag, str) or not etag.strip() or len(etag) > 500:
        raise ValidationError('A valid ETag is required.')
    uploaded_bytes = require_int(data.get('bytes'), 'bytes', minimum=1, maximum=MULTIPART_PART_SIZE)
    checksum = data.get('checksum')
    if checksum is not None and (not isinstance(checksum, str) or not checksum.strip() or len(checksum) > 200):
        raise ValidationError('checksum must be a non-empty string of 200 characters or less.')
    checksum = checksum.strip() if checksum else None
    part = acknowledge_part(session, part_number, etag.strip(), uploaded_bytes, checksum)
    return jsonify(part_payload(part)), 200


@uploads_bp.route('/uploads/multipart/<session_id>', methods=['GET'])
def get_multipart_session(session_id):
    session = _legacy_session_or_404(session_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    return jsonify(session_payload(session)), 200


def _create_multipart_item(session, actual_size):
    spec = spec_for_session(session)
    filename = session.object_key.rsplit('/', 1)[1]
    if spec is PERSONAL_LOG_TARGET:
        return _create_personal_log_from_object(
            session.member_id, filename, session.original_filename, session.title, actual_size,
        )
    return _create_media_from_object(
        session.song_id, session.rehearsal_id, filename, session.original_filename, actual_size,
    )


@uploads_bp.route('/uploads/multipart/<session_id>/complete', methods=['POST'])
def complete_multipart_media(session_id):
    session = _legacy_session_or_404(session_id, recover_aborting=False)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    item, status = complete_session(session, _object_size, _create_multipart_item)
    return jsonify(completion_payload(session, item)), status


@uploads_bp.route('/uploads/multipart/<session_id>/abort', methods=['POST'])
def abort_multipart_media(session_id):
    session = _legacy_session_or_404(session_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    return jsonify({'session_id': session.id, 'status': abort_session(session)}), 200
