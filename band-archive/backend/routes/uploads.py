from flask import Blueprint, jsonify, request, current_app

from extensions import db
from models import (Media, Song, Rehearsal, Member, PersonalLog, GalleryImage,
                    )
from errors import ValidationError, NotFoundError
from storage import storage
from media_processing import create_media, save_media_and_start
from multipart_upload_service import (
    MAX_MULTIPART_PARTS, MULTIPART_PART_SIZE, MEDIA_TARGET, PERSONAL_LOG_TARGET,
    abort_session, acknowledge_part, complete_session, completion_payload, create_session,
    get_session_or_404, issue_part, part_payload, recover_sessions, require_capability,
    require_active_session, session_payload, spec_for_session,
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


def _validate_media_target(song_id, rehearsal_id):
    song_id = _require_int(song_id, 'song_id', minimum=1)
    song = db.session.get(Song, song_id)
    if not song:
        raise NotFoundError('Song not found')
    if rehearsal_id is not None:
        rehearsal_id = _require_int(rehearsal_id, 'rehearsal_id', minimum=1)
        if not db.session.get(Rehearsal, rehearsal_id):
            raise NotFoundError('Rehearsal not found')
    return song_id, rehearsal_id


def _validate_personal_log_target(member_id):
    member_id = _require_int(member_id, 'member_id', minimum=1)
    if not db.session.get(Member, member_id):
        raise NotFoundError('Member not found')
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
    file_type = detect_file_type(filename)
    if file_type == 'video' and actual_size > MAX_VIDEO_BYTES:
        raise ValidationError('Video exceeds the 1 GiB upload limit.')
    media = create_media(
        song_id=song_id, rehearsal_id=rehearsal_id, filename=filename,
        original_filename=original_filename or None, file_type=file_type, file_size=actual_size,
    )
    return save_media_and_start(current_app._get_current_object(), media)


def _create_personal_log_from_object(member_id, filename, original_filename, title, actual_size):
    file_type = detect_file_type(filename)
    if file_type == 'video' and actual_size > MAX_VIDEO_BYTES:
        raise ValidationError('Video exceeds the 1 GiB upload limit.')
    from media_processing import create_personal_log, save_personal_log_and_start
    log = create_personal_log(
        member_id=member_id, title=title, filename=filename,
        original_filename=original_filename or None, file_type=file_type, file_size=actual_size,
    )
    return save_personal_log_and_start(current_app._get_current_object(), log)


def recover_multipart_upload_sessions(app):
    """Recover interrupted completion attempts and expire old R2 upload IDs.

    This runs on process startup.  It does not expose or regenerate legacy
    capabilities, so an old session without a capability remains inaccessible.
    """
    with app.app_context():
        recover_sessions()


@uploads_bp.route('/uploads/presign', methods=['POST'])
def presign():
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
    actual_size = _object_size(f'media/{filename}')
    declared_size = data.get('file_size')
    if declared_size is not None and _require_int(declared_size, 'file_size', minimum=0) != actual_size:
        raise ValidationError('Uploaded object size does not match file_size.')
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
    actual_size = _object_size(f'personal_logs/{filename}')
    declared_size = data.get('file_size')
    if declared_size is not None and _require_int(declared_size, 'file_size', minimum=0) != actual_size:
        raise ValidationError('Uploaded object size does not match file_size.')
    log = _create_personal_log_from_object(member_id, filename, original_filename, title, actual_size)
    return jsonify(log.to_dict()), 201


@uploads_bp.route('/uploads/complete/gallery', methods=['POST'])
def complete_gallery():
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
    image = GalleryImage(filename=filename, original_filename=original_filename or None, file_size=actual_size)
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
            member_id=member_id, title=title,
        )
    else:
        session, capability_token = create_session(
            MEDIA_TARGET, original_filename=filename, filename=secure_name,
            content_type=content_type, declared_bytes=declared_bytes,
            song_id=song_id, rehearsal_id=rehearsal_id,
        )
    return jsonify({
        'session_id': session.id, 'filename': secure_name, 'part_size': MULTIPART_PART_SIZE,
        'max_parts': MAX_MULTIPART_PARTS, 'expires_at': session.expires_at.isoformat(),
        'upload_capability_token': capability_token,
        'capability_header': UPLOAD_CAPABILITY_HEADER,
    }), 201


@uploads_bp.route('/uploads/multipart/<session_id>/parts', methods=['POST'])
def presign_multipart_part(session_id):
    session = require_active_session(session_id, request.headers.get(UPLOAD_CAPABILITY_HEADER))
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
    session = require_active_session(session_id, request.headers.get(UPLOAD_CAPABILITY_HEADER))
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
    session = get_session_or_404(session_id)
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
    session = get_session_or_404(session_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    item, status = complete_session(session, _object_size, _create_multipart_item)
    return jsonify(completion_payload(session, item)), status


@uploads_bp.route('/uploads/multipart/<session_id>/abort', methods=['POST'])
def abort_multipart_media(session_id):
    session = get_session_or_404(session_id)
    require_capability(session, request.headers.get(UPLOAD_CAPABILITY_HEADER))
    return jsonify({'session_id': session.id, 'status': abort_session(session)}), 200
