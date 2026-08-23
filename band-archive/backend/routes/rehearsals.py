import os
from datetime import date

from flask import Blueprint, jsonify, request, current_app

from extensions import db
from models import Rehearsal, Song, Media
from errors import ValidationError, NotFoundError
from storage import storage
from media_processing import create_media, save_media_and_start
from validators import (
    validate_required_string,
    validate_string_length,
    allowed_file,
    generate_secure_filename,
    detect_file_type,
    guess_content_type,
    ALLOWED_EXTENSIONS,
)

rehearsals_bp = Blueprint('rehearsals', __name__)


def _get_rehearsal_or_404(id):
    rehearsal = db.session.get(Rehearsal, id)
    if not rehearsal:
        raise NotFoundError()
    return rehearsal


def _parse_date(value, field_name):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValidationError(f"{field_name} must be a valid date (YYYY-MM-DD)")


@rehearsals_bp.route('/rehearsals', methods=['GET'])
def get_rehearsals():
    query = Rehearsal.query

    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)

    if year and month:
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)

        query = query.filter(
            # 단일 날짜가 해당 월에 속하거나
            (Rehearsal.date >= start) & (Rehearsal.date < end) |
            # 기간 일정이 해당 월과 겹치는 경우
            (Rehearsal.start_date.isnot(None)) & (Rehearsal.end_date.isnot(None)) &
            (Rehearsal.start_date < end) & (Rehearsal.end_date >= start)
        )

    rehearsals = query.order_by(Rehearsal.date.asc()).all()
    return jsonify([r.to_dict() for r in rehearsals])


@rehearsals_bp.route('/rehearsals/<int:id>', methods=['GET'])
def get_rehearsal(id):
    rehearsal = _get_rehearsal_or_404(id)
    return jsonify(rehearsal.to_dict())


@rehearsals_bp.route('/rehearsals', methods=['POST'])
def create_rehearsal():
    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    validate_required_string(data.get('title'), 'title')
    validate_string_length(data.get('title'), 'title', 200)

    rehearsal_date = _parse_date(data.get('date'), 'date')
    if not rehearsal_date:
        raise ValidationError("date is required")

    start_date = _parse_date(data.get('start_date'), 'start_date')
    end_date = _parse_date(data.get('end_date'), 'end_date')

    if start_date and end_date and start_date > end_date:
        raise ValidationError("start_date must be before or equal to end_date")

    location = data.get('location')
    if location:
        validate_string_length(location, 'location', 200)

    rehearsal = Rehearsal(
        title=data['title'],
        date=rehearsal_date,
        start_date=start_date,
        end_date=end_date,
        time=data.get('time'),
        location=location,
        latitude=data.get('latitude'),
        longitude=data.get('longitude'),
        memo=data.get('memo'),
        color=data.get('color', '#ffd32a'),
    )

    song_ids = data.get('song_ids', [])
    if song_ids:
        songs = Song.query.filter(Song.id.in_(song_ids)).all()
        rehearsal.songs = songs

    db.session.add(rehearsal)
    db.session.commit()
    return jsonify(rehearsal.to_dict()), 201


@rehearsals_bp.route('/rehearsals/<int:id>', methods=['PUT'])
def update_rehearsal(id):
    rehearsal = _get_rehearsal_or_404(id)

    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    if 'title' in data:
        validate_required_string(data['title'], 'title')
        validate_string_length(data['title'], 'title', 200)
        rehearsal.title = data['title']

    if 'date' in data:
        rehearsal_date = _parse_date(data['date'], 'date')
        if not rehearsal_date:
            raise ValidationError("date cannot be empty")
        rehearsal.date = rehearsal_date

    if 'start_date' in data:
        rehearsal.start_date = _parse_date(data['start_date'], 'start_date')

    if 'end_date' in data:
        rehearsal.end_date = _parse_date(data['end_date'], 'end_date')

    if rehearsal.start_date and rehearsal.end_date and rehearsal.start_date > rehearsal.end_date:
        raise ValidationError("start_date must be before or equal to end_date")

    if 'time' in data:
        rehearsal.time = data['time']
    if 'location' in data:
        if data['location']:
            validate_string_length(data['location'], 'location', 200)
        rehearsal.location = data['location']
    if 'latitude' in data:
        rehearsal.latitude = data['latitude']
    if 'longitude' in data:
        rehearsal.longitude = data['longitude']
    if 'memo' in data:
        rehearsal.memo = data['memo']
    if 'color' in data:
        rehearsal.color = data['color']

    if 'song_ids' in data:
        songs = Song.query.filter(Song.id.in_(data['song_ids'])).all()
        rehearsal.songs = songs

    db.session.commit()
    return jsonify(rehearsal.to_dict())


@rehearsals_bp.route('/rehearsals/<int:id>', methods=['DELETE'])
def delete_rehearsal(id):
    rehearsal = _get_rehearsal_or_404(id)
    db.session.delete(rehearsal)
    db.session.commit()
    return jsonify({"message": "Rehearsal deleted"}), 200



@rehearsals_bp.route('/rehearsals/<int:id>/media', methods=['GET'])
def get_rehearsal_media(id):
    rehearsal = _get_rehearsal_or_404(id)
    return jsonify([m.to_dict() for m in rehearsal.media_files])


@rehearsals_bp.route('/rehearsals/<int:id>/media', methods=['POST'])
def upload_rehearsal_media(id):
    rehearsal = _get_rehearsal_or_404(id)

    song_id = request.form.get('song_id', type=int)
    if not song_id:
        raise ValidationError("song_id is required")
    song = db.session.get(Song, song_id)
    if not song:
        raise NotFoundError("Song not found")

    if 'file' not in request.files:
        raise ValidationError("No file provided")

    file = request.files['file']
    if file.filename == '':
        raise ValidationError("No file selected")

    if not allowed_file(file.filename):
        raise ValidationError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    filename = generate_secure_filename(file.filename)
    content_type = guess_content_type(filename)

    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    storage.upload(f'media/{filename}', file, content_type=content_type)

    media = create_media(
        song_id=song_id,
        rehearsal_id=id,
        filename=filename,
        original_filename=file.filename,
        file_type=detect_file_type(filename),
        file_size=file_size,
    )
    save_media_and_start(current_app._get_current_object(), media)
    return jsonify(media.to_dict()), 201
