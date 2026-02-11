import os

from flask import Blueprint, jsonify, request, current_app, send_from_directory

from extensions import db
from models import Member, PersonalLog
from errors import NotFoundError, ValidationError
from validators import (
    validate_required_string,
    validate_string_length,
    allowed_file,
    generate_secure_filename,
    ALLOWED_EXTENSIONS,
)

personal_logs_bp = Blueprint('personal_logs', __name__)

PERSONAL_LOGS_SUBDIR = 'personal_logs'

AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'}
VIDEO_EXTENSIONS = {'mp4', 'webm', 'mov', 'avi', 'mkv'}
ALLOWED_LOG_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def _get_member_or_404(member_id):
    member = db.session.get(Member, member_id)
    if not member:
        raise NotFoundError("Member not found")
    return member


def _get_upload_dir():
    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], PERSONAL_LOGS_SUBDIR)
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


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
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_LOG_EXTENSIONS))}")

    filename = generate_secure_filename(file.filename)
    upload_dir = _get_upload_dir()
    file_path = os.path.join(upload_dir, filename)
    file.save(file_path)
    os.chmod(file_path, 0o644)

    log = PersonalLog(
        member_id=member_id,
        title=title,
        filename=filename,
        original_filename=file.filename,
        file_type=_detect_file_type(file.filename),
    )
    db.session.add(log)
    db.session.commit()
    return jsonify(log.to_dict()), 201


@personal_logs_bp.route('/personal-logs/<int:log_id>', methods=['DELETE'])
def delete_log(log_id):
    log = db.session.get(PersonalLog, log_id)
    if not log:
        raise NotFoundError("Personal log not found")

    upload_dir = _get_upload_dir()
    file_path = os.path.join(upload_dir, log.filename)
    if os.path.exists(file_path):
        os.remove(file_path)

    db.session.delete(log)
    db.session.commit()
    return jsonify({"message": "Personal log deleted"}), 200


@personal_logs_bp.route('/uploads/personal_logs/<filename>')
def serve_personal_log_file(filename):
    upload_dir = _get_upload_dir()
    return send_from_directory(upload_dir, filename)
