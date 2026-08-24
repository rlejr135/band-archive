import secrets
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from flask import Blueprint, jsonify, request, current_app

from extensions import db
from models import (Media, Song, Rehearsal, Member, PersonalLog, GalleryImage,
                    MultipartUploadSession, MultipartUploadPart,
                    PersonalLogMultipartUploadSession, PersonalLogMultipartUploadPart)
from errors import ValidationError, NotFoundError
from storage import storage
from media_processing import create_media, save_media_and_start
from validators import allowed_file, generate_secure_filename, detect_file_type, guess_content_type

uploads_bp = Blueprint('uploads', __name__)

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
MULTIPART_PART_SIZE = 16 * 1024 * 1024
MAX_MULTIPART_PARTS = 10_000
UPLOAD_CAPABILITY_HEADER = 'X-Upload-Capability'


def _allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


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


def _get_session_or_404(session_id):
    session = db.session.get(MultipartUploadSession, session_id)
    if not session:
        session = db.session.get(PersonalLogMultipartUploadSession, session_id)
    if not session:
        raise NotFoundError('Upload session not found')
    _expire_session_if_needed(session)
    return session


def _is_personal_session(session):
    return isinstance(session, PersonalLogMultipartUploadSession)


def _session_part_model(session):
    return PersonalLogMultipartUploadPart if _is_personal_session(session) else MultipartUploadPart


def _now():
    """Use naive UTC because SQLite strips timezone information."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _expires_at_utc_naive(session):
    expires_at = session.expires_at
    if expires_at and expires_at.tzinfo is not None:
        return expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    return expires_at


def _expire_session_if_needed(session):
    if session.status not in ('initiated', 'completing') or _expires_at_utc_naive(session) > _now():
        return False
    # Abort is best-effort here.  The terminal DB state prevents new part URLs;
    # R2 bucket lifecycle remains the final cleanup guard if this request fails.
    try:
        storage.abort_multipart_upload(session.object_key, session.r2_upload_id)
    except Exception:
        current_app.logger.exception('Failed to abort expired multipart upload %s', session.id)
    session.status = 'expired'
    session.completion_started_at = None
    db.session.commit()
    return True


def recover_multipart_upload_sessions(app):
    """Recover interrupted completion attempts and expire old R2 upload IDs.

    This runs on process startup.  It does not expose or regenerate legacy
    capabilities, so an old session without a capability remains inaccessible.
    """
    with app.app_context():
        for model in (MultipartUploadSession, PersonalLogMultipartUploadSession):
            for session in model.query.filter_by(status='completing').all():
                if _expires_at_utc_naive(session) > _now():
                    session.status = 'initiated'
                    session.completion_started_at = None
            db.session.commit()
            for session in model.query.filter(model.status.in_(('initiated', 'completing'))).all():
                _expire_session_if_needed(session)


def _require_session_capability(session):
    """Temporary ownership boundary until authenticated user ownership exists."""
    token = request.headers.get(UPLOAD_CAPABILITY_HEADER)
    if not token or not session.capability_token_hash:
        raise ValidationError('A valid upload capability is required.', status_code=403)
    if not check_password_hash(session.capability_token_hash, token):
        raise ValidationError('A valid upload capability is required.', status_code=403)


def _require_active_session(session_id):
    session = _get_session_or_404(session_id)
    _require_session_capability(session)
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    return session


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
    if upload_type == 'gallery' and not _allowed_image(filename):
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
    if not _allowed_image(filename):
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
    object_key = f"{'personal_logs' if has_member else 'media'}/{secure_name}"
    try:
        r2_upload_id = storage.create_multipart_upload(object_key, content_type)
    except Exception as exc:
        raise ValidationError('Multipart upload could not be initiated.') from exc
    # This random value is returned exactly once.  Only its password hash is
    # persisted, and all later session operations require it in the header.
    capability_token = secrets.token_urlsafe(32)
    capability_token_hash = generate_password_hash(capability_token)
    if has_member:
        session = PersonalLogMultipartUploadSession(
            r2_upload_id=r2_upload_id, object_key=object_key, original_filename=filename,
            title=title, content_type=content_type, declared_bytes=declared_bytes, member_id=member_id,
            capability_token_hash=capability_token_hash,
        )
    else:
        session = MultipartUploadSession(
            r2_upload_id=r2_upload_id, object_key=object_key, original_filename=filename,
            content_type=content_type, declared_bytes=declared_bytes, song_id=song_id, rehearsal_id=rehearsal_id,
            capability_token_hash=capability_token_hash,
        )
    db.session.add(session)
    db.session.commit()
    return jsonify({
        'session_id': session.id, 'filename': secure_name, 'part_size': MULTIPART_PART_SIZE,
        'max_parts': MAX_MULTIPART_PARTS, 'expires_at': session.expires_at.isoformat(),
        'upload_capability_token': capability_token,
        'capability_header': UPLOAD_CAPABILITY_HEADER,
    }), 201


@uploads_bp.route('/uploads/multipart/<session_id>/parts', methods=['POST'])
def presign_multipart_part(session_id):
    session = _require_active_session(session_id)
    data = request.get_json()
    if not data:
        raise ValidationError('Request body is required')
    part_number = _require_int(data.get('part_number'), 'part_number', minimum=1, maximum=MAX_MULTIPART_PARTS)
    part_model = _session_part_model(session)
    part = part_model.query.filter_by(session_id=session.id, part_number=part_number).first()
    if part and part.acknowledged_at:
        return jsonify(_part_payload(part)), 409
    if not part:
        part = part_model(session_id=session.id, part_number=part_number)
        db.session.add(part)
        db.session.commit()
    try:
        upload_url = storage.generate_upload_part_url(session.object_key, session.r2_upload_id, part_number)
    except Exception as exc:
        raise ValidationError('Part upload URL could not be created.') from exc
    payload = _part_payload(part)
    payload.update({'upload_url': upload_url, 'status': 'issued'})
    return jsonify(payload), 200


def _part_payload(part):
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


def _acknowledged_completion_parts(session):
    parts = list(session.parts)
    if not parts:
        raise ValidationError('At least one acknowledged part is required.', status_code=409)
    unacknowledged = [part.part_number for part in parts if not part.acknowledged_at]
    if unacknowledged:
        raise ValidationError('All issued parts must be acknowledged before completion.', status_code=409)
    if sum(part.uploaded_bytes for part in parts) != session.declared_bytes:
        raise ValidationError('Acknowledged part bytes do not match declared_bytes.')
    return [
        {'PartNumber': part.part_number, 'ETag': part.etag}
        for part in sorted(parts, key=lambda item: item.part_number)
    ]


@uploads_bp.route('/uploads/multipart/<session_id>/parts/<int:part_number>/ack', methods=['POST'])
def acknowledge_multipart_part(session_id, part_number):
    session = _require_active_session(session_id)
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
    part_model = _session_part_model(session)
    part = part_model.query.filter_by(session_id=session.id, part_number=part_number).first()
    if not part:
        raise ValidationError('part_number has not been issued.', status_code=409)
    if part.acknowledged_at:
        if part.etag == etag.strip() and part.uploaded_bytes == uploaded_bytes and part.checksum == checksum:
            return jsonify(_part_payload(part)), 200
        raise ValidationError('Part acknowledgement conflicts with the stored result.', status_code=409)
    part.etag = etag.strip()
    part.uploaded_bytes = uploaded_bytes
    part.checksum = checksum
    part.acknowledged_at = _now()
    db.session.commit()
    return jsonify(_part_payload(part)), 200


def _find_completed_item(session):
    if _is_personal_session(session):
        if session.personal_log_id:
            return db.session.get(PersonalLog, session.personal_log_id)
        return PersonalLog.query.filter_by(
            member_id=session.member_id, filename=session.object_key.rsplit('/', 1)[1],
        ).order_by(PersonalLog.id.desc()).first()
    if session.media_id:
        return db.session.get(Media, session.media_id)
    return Media.query.filter_by(
        song_id=session.song_id, rehearsal_id=session.rehearsal_id,
        filename=session.object_key.rsplit('/', 1)[1],
    ).order_by(Media.id.desc()).first()


def _completion_payload(session, item):
    key = 'personal_log' if _is_personal_session(session) else 'media'
    return {'session_id': session.id, 'status': 'completed', key: item.to_dict() if item else None}


def _mark_session_completed(session, item):
    session.status = 'completed'
    session.completion_started_at = None
    session.completed_at = _now()
    if _is_personal_session(session):
        session.personal_log_id = item.id
    else:
        session.media_id = item.id
    db.session.commit()


def _session_payload(session):
    target = (
        {'kind': 'personal_log', 'member_id': session.member_id, 'title': session.title}
        if _is_personal_session(session) else
        {'kind': 'media', 'song_id': session.song_id, 'rehearsal_id': session.rehearsal_id}
    )
    item = _find_completed_item(session) if session.status == 'completed' else None
    result_key = 'personal_log' if _is_personal_session(session) else 'media'
    return {
        'session_id': session.id,
        'status': session.status,
        'target': target,
        'declared_bytes': session.declared_bytes,
        'part_size': MULTIPART_PART_SIZE,
        'expires_at': session.expires_at.isoformat() if session.expires_at else None,
        'completed_at': session.completed_at.isoformat() if session.completed_at else None,
        'result': {result_key: item.to_dict()} if item else None,
        'parts': [_part_payload(part) for part in session.parts],
    }


@uploads_bp.route('/uploads/multipart/<session_id>', methods=['GET'])
def get_multipart_session(session_id):
    session = _get_session_or_404(session_id)
    _require_session_capability(session)
    return jsonify(_session_payload(session)), 200


@uploads_bp.route('/uploads/multipart/<session_id>/complete', methods=['POST'])
def complete_multipart_media(session_id):
    session = _get_session_or_404(session_id)
    _require_session_capability(session)
    if session.status == 'completed':
        return jsonify(_completion_payload(session, _find_completed_item(session))), 200
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    parts = _acknowledged_completion_parts(session)
    session_model = PersonalLogMultipartUploadSession if _is_personal_session(session) else MultipartUploadSession
    claimed = session_model.query.filter_by(id=session.id, status='initiated').update({
        'status': 'completing', 'completion_started_at': _now(),
    })
    db.session.commit()
    if not claimed:
        raise ValidationError('Upload session is already being completed.', status_code=409)
    session = db.session.get(session_model, session.id)
    try:
        item = _find_completed_item(session)
        if item:
            _mark_session_completed(session, item)
            return jsonify(_completion_payload(session, item)), 200
        try:
            storage.complete_multipart_upload(session.object_key, session.r2_upload_id, parts)
        except Exception as complete_error:
            # A timeout can happen after R2 accepted CompleteMultipartUpload.
            # A matching final object is sufficient to finish the durable DB work.
            try:
                if _object_size(session.object_key) != session.declared_bytes:
                    raise complete_error
            except ValidationError:
                raise complete_error
        actual_size = _object_size(session.object_key)
        if actual_size != session.declared_bytes:
            raise ValidationError('Uploaded object size does not match declared_bytes.')
        if _is_personal_session(session):
            item = _create_personal_log_from_object(
                session.member_id, session.object_key.rsplit('/', 1)[1], session.original_filename,
                session.title, actual_size,
            )
        else:
            item = _create_media_from_object(
                session.song_id, session.rehearsal_id, session.object_key.rsplit('/', 1)[1],
                session.original_filename, actual_size,
            )
        _mark_session_completed(session, item)
    except ValidationError:
        db.session.rollback()
        session = db.session.get(session_model, session.id)
        if session and session.status == 'completing':
            session.status = 'failed'
            session.completion_started_at = None
            db.session.commit()
        raise
    except Exception:
        db.session.rollback()
        session = db.session.get(session_model, session.id)
        if session and session.status == 'completing':
            # Transport/R2 errors are retryable; persisted acknowledgements are
            # retained and the next complete call claims the session again.
            session.status = 'initiated'
            session.completion_started_at = None
            db.session.commit()
        raise
    return jsonify(_completion_payload(session, item)), 201


@uploads_bp.route('/uploads/multipart/<session_id>/abort', methods=['POST'])
def abort_multipart_media(session_id):
    session = _get_session_or_404(session_id)
    _require_session_capability(session)
    if session.status == 'aborted':
        return jsonify({'session_id': session.id, 'status': 'aborted'}), 200
    if session.status == 'completed':
        return jsonify({'session_id': session.id, 'status': 'completed'}), 200
    if session.status == 'expired':
        return jsonify({'session_id': session.id, 'status': 'expired'}), 200
    if session.status != 'initiated':
        raise ValidationError(f'Upload session is {session.status}.', status_code=409)
    try:
        storage.abort_multipart_upload(session.object_key, session.r2_upload_id)
    except Exception as exc:
        raise ValidationError('Multipart upload could not be aborted.') from exc
    session.status = 'aborted'
    db.session.commit()
    return jsonify({'session_id': session.id, 'status': 'aborted'}), 200
