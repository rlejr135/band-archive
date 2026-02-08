import os

from flask import Blueprint, jsonify, request, current_app
from werkzeug.utils import secure_filename

from extensions import db
from models import Song, PracticeLog
from errors import NotFoundError, ValidationError
from validators import allowed_file, ALLOWED_EXTENSIONS

practice_logs_bp = Blueprint('practice_logs', __name__)


def _get_song_or_404(song_id):
    song = db.session.get(Song, song_id)
    if not song:
        raise NotFoundError("Song not found")
    return song


def _get_practice_log_or_404(id):
    log = db.session.get(PracticeLog, id)
    if not log:
        raise NotFoundError("Practice log not found")
    return log


@practice_logs_bp.route('/songs/<int:song_id>/practice-logs', methods=['GET'])
def get_practice_logs(song_id):
    _get_song_or_404(song_id)
    logs = PracticeLog.query.filter_by(song_id=song_id).order_by(PracticeLog.date.desc()).all()
    return jsonify([log.to_dict() for log in logs])


@practice_logs_bp.route('/songs/<int:song_id>/practice-logs', methods=['POST'])
def create_practice_log(song_id):
    _get_song_or_404(song_id)

    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    log = PracticeLog(
        song_id=song_id,
        content=data.get('content'),
        feedback=data.get('feedback'),
    )
    db.session.add(log)
    db.session.commit()
    return jsonify(log.to_dict()), 201


@practice_logs_bp.route('/practice-logs/<int:id>', methods=['GET'])
def get_practice_log(id):
    log = _get_practice_log_or_404(id)
    return jsonify(log.to_dict())


@practice_logs_bp.route('/practice-logs/<int:id>', methods=['PUT'])
def update_practice_log(id):
    log = _get_practice_log_or_404(id)

    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    if 'content' in data:
        log.content = data['content']
    if 'feedback' in data:
        log.feedback = data['feedback']

    db.session.commit()
    return jsonify(log.to_dict())


@practice_logs_bp.route('/practice-logs/<int:id>', methods=['DELETE'])
def delete_practice_log(id):
    log = _get_practice_log_or_404(id)
    db.session.delete(log)
    db.session.commit()
    return jsonify({"message": "Practice log deleted"}), 200


@practice_logs_bp.route('/practice-logs/<int:id>/upload', methods=['POST'])
def upload_recording(id):
    log = _get_practice_log_or_404(id)

    if 'file' not in request.files:
        raise ValidationError("No file provided")

    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")

    if not allowed_file(file.filename):
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    filename = secure_filename(file.filename)
    filename = f"practice_{id}_{filename}"
    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))

    log.recording = filename
    db.session.commit()
    return jsonify(log.to_dict()), 200
