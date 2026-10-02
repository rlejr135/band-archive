from functools import wraps

from flask import Blueprint, after_this_request, jsonify, request, current_app
from sqlalchemy import func, inspect, text

from auth_service import current_app_user
from band_service import ACTIVE
from extensions import db
from models import (Band, BandMembership, Media, Song, Rehearsal, Member,
                    PersonalLog, GalleryImage, MultipartUploadSession,
                    PersonalLogMultipartUploadSession)
from legacy_scope import legacy_band_id, legacy_get_or_404, validate_legacy_graph
from errors import ValidationError, NotFoundError
from storage import storage
from media_processing import (create_media, create_personal_log, save_media_and_start,
                              save_personal_log_and_start)
from upload_record_service import create_uploaded_record
from multipart_upload_service import (
    MAX_MULTIPART_PARTS, MULTIPART_PART_SIZE, MEDIA_TARGET, PERSONAL_LOG_TARGET,
    abort_session, acknowledge_part, complete_session, completion_payload, create_session,
    expire_session_if_needed,
    get_session_or_404, issue_part, part_payload, recover_sessions, require_capability,
    session_payload, spec_for_session,
)
from validators import (allowed_file, allowed_image_file, generate_secure_filename,
                        detect_file_type, guess_content_type)

uploads_bp = Blueprint('uploads', __name__)

MAX_VIDEO_BYTES = 1024 * 1024 * 1024
UPLOAD_CAPABILITY_HEADER = 'X-Upload-Capability'


def _require_int(value, field_name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f'{field_name} must be an integer')
    if minimum is not None and value < minimum:
        raise ValidationError(f'{field_name} must be at least {minimum}')
    if maximum is not None and value > maximum:
        raise ValidationError(f'{field_name} must be at most {maximum}')
    return value


def _legacy_upload_band_id():
    """Resolve the only tenant v1 upload endpoints are allowed to mutate."""
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


def _v2_active_membership(band_id, *, lock=False):
    """Resolve the current actor only as an active member of this URL band."""
    user = current_app_user(required=True)
    band_query = Band.query.filter(Band.id == band_id, Band.deleted_at.is_(None))
    member_query = BandMembership.query.filter_by(band_id=band_id, user_id=user.id, status=ACTIVE)
    if lock and db.engine.dialect.name != 'sqlite':
        band_query = band_query.with_for_update()
        member_query = member_query.with_for_update()
    band = band_query.one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    membership = member_query.one_or_none()
    if membership is None:
        raise ValidationError('An active band membership is required.', status_code=403)
    return band, membership


def _v2_begin_write():
    if db.engine.dialect.name == 'sqlite':
        db.session.execute(text('BEGIN IMMEDIATE'))


def _v2_write(operation):
    try:
        _v2_begin_write()
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def _v2_session_or_404(session_id, band_id, *, recover_aborting=True):
    session = get_session_or_404(
        session_id, band_id=band_id, recover_aborting=recover_aborting,
    )
    if session.is_legacy:
        raise NotFoundError('Upload session not found')
    return session


def _v2_used_and_reserved_bytes(band_id):
    """Logical object usage plus every still-live multipart reservation."""
    used = sum((
        db.session.query(func.coalesce(func.sum(Media.file_size), 0)).filter_by(band_id=band_id).scalar() or 0,
        db.session.query(func.coalesce(func.sum(PersonalLog.file_size), 0)).filter_by(band_id=band_id).scalar() or 0,
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
    ))
    return int(used), int(reserved)


def _v2_media_target(band_id, data):
    song_id = _require_int(data.get('song_id'), 'song_id', minimum=1)
    song = Song.query.filter_by(id=song_id, band_id=band_id).one_or_none()
    if song is None:
        raise NotFoundError('Song not found')
    rehearsal_id = data.get('rehearsal_id')
    if rehearsal_id is not None:
        rehearsal_id = _require_int(rehearsal_id, 'rehearsal_id', minimum=1)
        if Rehearsal.query.filter_by(id=rehearsal_id, band_id=band_id).one_or_none() is None:
            raise NotFoundError('Rehearsal not found')
    return song_id, rehearsal_id


def _v2_personal_log_target(band_id, member_id):
    member_id = _require_int(member_id, 'member_id', minimum=1)
    if Member.query.filter_by(id=member_id, band_id=band_id).one_or_none() is None:
        raise NotFoundError('Member not found')
    return member_id


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
    song_id = _require_int(song_id, 'song_id', minimum=1)
    song = legacy_get_or_404(Song, song_id, 'Song not found')
    if rehearsal_id is not None:
        rehearsal_id = _require_int(rehearsal_id, 'rehearsal_id', minimum=1)
        legacy_get_or_404(Rehearsal, rehearsal_id, 'Rehearsal not found')
    return song_id, rehearsal_id


def _validate_personal_log_target(member_id):
    legacy_band_id = _legacy_upload_band_id()
    member_id = _require_int(member_id, 'member_id', minimum=1)
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
    if declared_size is not None and _require_int(declared_size, 'file_size', minimum=0) != actual_size:
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


def _v2_multipart_item(session, actual_size):
    """Materialize only the verified, tenant-scoped object at completion."""
    filename = session.object_key.rsplit('/', 1)[1]
    band = db.session.get(Band, session.band_id)
    spec = spec_for_session(session)
    fields = {
        'band_id': session.band_id,
        'created_by_membership_id': session.created_by_membership_id,
        'visibility': band.default_visibility,
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


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/multipart/initiate', methods=['POST'])
@_v2_upload_write
def v2_initiate_multipart_upload(band_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError('A JSON object is required.')
    filename = data.get('filename', '').strip() if isinstance(data.get('filename'), str) else ''
    if not filename or not allowed_file(filename) or detect_file_type(filename) != 'video':
        raise ValidationError('A supported video filename is required.')
    declared_bytes = _require_int(data.get('declared_bytes'), 'declared_bytes', minimum=1, maximum=MAX_VIDEO_BYTES)
    has_member = data.get('member_id') is not None
    has_song_target = data.get('song_id') is not None or data.get('rehearsal_id') is not None
    if has_member == has_song_target:
        raise ValidationError('Exactly one media or personal-log target is required.')
    content_type = data.get('content_type', '').strip() if isinstance(data.get('content_type'), str) else ''
    secure_name = generate_secure_filename(filename)

    def operation():
        band, membership = _v2_active_membership(band_id, lock=True)
        used, reserved = _v2_used_and_reserved_bytes(band.id)
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
            member_id = _v2_personal_log_target(band.id, data.get('member_id'))
            common['object_key'] = common['object_key'].format(prefix='personal_logs')
            return create_session(PERSONAL_LOG_TARGET, member_id=member_id, title=title or filename, **common)
        song_id, rehearsal_id = _v2_media_target(band.id, data)
        common['object_key'] = common['object_key'].format(prefix='media')
        return create_session(MEDIA_TARGET, song_id=song_id, rehearsal_id=rehearsal_id, **common)

    session, capability_token = _v2_write(operation)
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
    part, upload_url = issue_part(session, _require_int(data.get('part_number'), 'part_number', minimum=1, maximum=MAX_MULTIPART_PARTS))
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
    part = acknowledge_part(session, _require_int(part_number, 'part_number', minimum=1, maximum=MAX_MULTIPART_PARTS),
                            etag.strip(), _require_int(size, 'bytes', minimum=1, maximum=MULTIPART_PART_SIZE),
                            checksum.strip() if checksum else None)
    return jsonify(part_payload(part)), 200


@uploads_bp.route('/api/v2/bands/<int:band_id>/uploads/multipart/<session_id>/complete', methods=['POST'])
@_v2_upload_write
def v2_complete_multipart_upload(band_id, session_id):
    def operation():
        _v2_active_membership(band_id, lock=True)  # Recheck at materialization time.
        session = _v2_session_or_404(session_id, band_id, recover_aborting=False)
        require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
        return complete_session(session, _object_size, _v2_multipart_item)
    item, status = _v2_write(operation)
    session = _v2_session_or_404(session_id, band_id)
    return jsonify(completion_payload(session, item)), status


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
    if declared_size is not None and _require_int(declared_size, 'file_size', minimum=0) != actual_size:
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
    declared_bytes = _require_int(data.get('declared_bytes'), 'declared_bytes', minimum=1, maximum=MAX_VIDEO_BYTES)
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
    part_number = _require_int(data.get('part_number'), 'part_number', minimum=1, maximum=MAX_MULTIPART_PARTS)
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
    _require_int(part_number, 'part_number', minimum=1, maximum=MAX_MULTIPART_PARTS)
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')
    etag = data.get('etag')
    if not isinstance(etag, str) or not etag.strip() or len(etag) > 500:
        raise ValidationError('A valid ETag is required.')
    uploaded_bytes = _require_int(data.get('bytes'), 'bytes', minimum=1, maximum=MULTIPART_PART_SIZE)
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
