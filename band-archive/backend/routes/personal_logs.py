import os
import mimetypes

from flask import Blueprint, jsonify, request, redirect

from extensions import db
from models import Member, PersonalLog
from errors import NotFoundError, ValidationError
from storage import storage
from validators import (
    validate_required_string,
    validate_string_length,
    generate_secure_filename,
)

personal_logs_bp = Blueprint('personal_logs', __name__)

AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'}
VIDEO_EXTENSIONS = {'mp4', 'webm', 'mov', 'avi', 'mkv'}
ALLOWED_LOG_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def _get_member_or_404(member_id):
    member = db.session.get(Member, member_id)
    if not member:
        raise NotFoundError("Member not found")
    return member


def _detect_file_type(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    return 'audio'


@personal_logs_bp.route('/members/<int:member_id>/logs', methods=['GET'])
def get_logs(member_id):
    _get_member_or_404(member_id)
    logs = PersonalLog.query.filter_by(member_id=member_id).order_by(PersonalLog.created_at.desc()).all()
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

    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    storage.upload(f'personal_logs/{filename}', file, content_type=content_type)

    log = PersonalLog(
        member_id=member_id,
        title=title,
        filename=filename,
        original_filename=file.filename,
        file_type=_detect_file_type(file.filename),
        file_size=file_size,
    )
    db.session.add(log)
    db.session.commit()
    return jsonify(log.to_dict()), 201


@personal_logs_bp.route('/personal-logs/<int:log_id>', methods=['DELETE'])
def delete_log(log_id):
    log = db.session.get(PersonalLog, log_id)
    if not log:
        raise NotFoundError("Personal log not found")

    storage.delete(f'personal_logs/{log.filename}')

    db.session.delete(log)
    db.session.commit()
    return jsonify({"message": "Personal log deleted"}), 200


@personal_logs_bp.route('/uploads/personal_logs/<filename>')
def serve_personal_log_file(filename):
    """하위 호환: presigned URL로 리다이렉트"""
    url = storage.generate_url(f'personal_logs/{filename}')
    return redirect(url)
