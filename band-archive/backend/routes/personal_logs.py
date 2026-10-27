import mimetypes

from flask import Blueprint, jsonify, request, redirect, current_app

from extensions import db
from models import Member, PersonalLog
from errors import NotFoundError, ValidationError
from legacy_scope import (legacy_get_or_404, legacy_query, reject_foreign_children,
                          reject_legacy_destructive_write, validate_legacy_graphs)
from upload_helpers import prepare_upload
from storage import storage
from media_processing import (create_personal_log, save_personal_log_and_start,
                              retry_audio_processing_record, PERSONAL_LOG_SPEC,
                              delete_original_and_audio, processing_status_response)
from media_sources import select_media_source_keys
from validators import (
    validate_required_string,
    validate_string_length,
    generate_secure_filename,
    detect_file_type,
)
from legacy_read_visibility import legacy_item_visible, legacy_read_scope
from content_visibility import personal_log_visibility

personal_logs_bp = Blueprint('personal_logs', __name__)

AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'}
VIDEO_EXTENSIONS = {'mp4', 'webm', 'mov', 'avi', 'mkv'}
ALLOWED_LOG_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def _get_member_or_404(member_id):
    return legacy_get_or_404(Member, member_id, "Member not found")


def _get_log_or_404(log_id):
    return legacy_get_or_404(PersonalLog, log_id, "Personal log not found")


def _get_visible_log_or_404(log_id):
    log = _get_log_or_404(log_id)
    validate_legacy_graphs([log], 'Personal log not found')
    scope = legacy_read_scope(log.band_id, roster=True)
    if not legacy_item_visible(scope, personal_log_visibility(scope, log)):
        raise NotFoundError('Personal log not found')
    return log



@personal_logs_bp.route('/members/<int:member_id>/logs', methods=['GET'])
def get_logs(member_id):
    member = _get_member_or_404(member_id)
    validate_legacy_graphs([member], 'Member not found')
    scope = legacy_read_scope(member.band_id, roster=True)
    logs = legacy_query(PersonalLog).filter(PersonalLog.member_id == member_id).order_by(
        PersonalLog.created_at.desc(),
    ).all()
    validate_legacy_graphs(logs, 'Personal log not found')
    logs = [log for log in logs if legacy_item_visible(
        scope, personal_log_visibility(scope, log),
    )]
    return jsonify([log.to_dict() for log in logs])


@personal_logs_bp.route('/members/<int:member_id>/logs', methods=['POST'])
def create_log(member_id):
    _get_member_or_404(member_id)

    title = request.form.get('title', '').strip()
    validate_required_string(title, 'title')
    validate_string_length(title, 'title', 200)

    if 'file' not in request.files:
        raise ValidationError("No file provided")

    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")

    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_LOG_EXTENSIONS:
        # Fallback: Content-Type 헤더로 판단 (모바일에서 확장자 없이 MIME만 보내는 경우)
        ct = (file.content_type or '').lower()
        if ct.startswith('audio/') or ct.startswith('video/'):
            mime_sub = ct.split('/')[-1]
            mime_ext_map = {'mpeg': 'mp3', 'x-m4a': 'm4a', 'mp4': 'mp4', 'quicktime': 'mov', 'x-wav': 'wav'}
            ext = mime_ext_map.get(mime_sub, mime_sub)
        if ext not in ALLOWED_LOG_EXTENSIONS:
            raise ValidationError(f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_LOG_EXTENSIONS))}")

    filename = generate_secure_filename(file.filename)
    content_type, _ = mimetypes.guess_type(filename)
    upload = prepare_upload(file, filename, content_type)
    storage.upload(f'personal_logs/{upload.filename}', file, content_type=upload.content_type)

    log = create_personal_log(
        member_id=member_id,
        title=title,
        filename=upload.filename,
        original_filename=upload.original_filename,
        file_type=detect_file_type(file.filename),
        file_size=upload.file_size,
    )
    save_personal_log_and_start(current_app._get_current_object(), log)
    return jsonify(log.to_dict()), 201


@personal_logs_bp.route('/personal-logs/<int:log_id>', methods=['DELETE'])
def delete_log(log_id):
    reject_legacy_destructive_write()
    log = _get_log_or_404(log_id)
    from models import Comment
    reject_foreign_children(log, Comment, 'personal_log_id', 'Personal log not found')

    delete_original_and_audio(log, 'personal_logs', current_app.logger)

    db.session.delete(log)
    db.session.commit()
    return jsonify({"message": "Personal log deleted"}), 200


@personal_logs_bp.route('/personal-logs/<int:log_id>/processing', methods=['GET'])
def get_log_processing(log_id):
    log = _get_visible_log_or_404(log_id)
    return jsonify(processing_status_response(log, 'personal_logs'))


@personal_logs_bp.route('/personal-logs/<int:log_id>/retry-audio', methods=['POST'])
def retry_log_audio(log_id):
    log = _get_log_or_404(log_id)
    try:
        retry_audio_processing_record(current_app._get_current_object(), log, PERSONAL_LOG_SPEC)
    except ValueError as exc:
        raise ValidationError(str(exc))
    except RuntimeError as exc:
        raise ValidationError(str(exc), status_code=409)
    return jsonify({'id': log.id, 'status': log.transcoding_status}), 202


@personal_logs_bp.route('/uploads/personal_logs/<filename>')
def serve_personal_log_file(filename):
    """하위 호환: presigned URL로 리다이렉트"""
    requested_key = f'personal_logs/{filename}'
    records = legacy_query(PersonalLog).filter(PersonalLog.filename == filename).all()
    from deletion_service import is_pending_deletion
    validate_legacy_graphs(records, 'Personal log source not found')
    records = [record for record in records if not is_pending_deletion(
        'personal_log', record.id, record.band_id,
    )]
    readable = []
    for record in records:
        scope = legacy_read_scope(record.band_id, roster=True)
        if legacy_item_visible(scope, personal_log_visibility(scope, record)):
            readable.append(record)
    if not any(select_media_source_keys(record, 'personal_logs')['original'] == requested_key
               for record in readable):
        raise NotFoundError('Personal log source not found')
    url = storage.generate_url(requested_key)
    return redirect(url)
