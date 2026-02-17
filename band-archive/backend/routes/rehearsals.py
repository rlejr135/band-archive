from datetime import date

from flask import Blueprint, jsonify, request

from extensions import db
from models import Rehearsal, Song
from errors import ValidationError, NotFoundError
from validators import validate_required_string, validate_string_length

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

    rehearsal = Rehearsal(
        title=data['title'],
        date=rehearsal_date,
        start_date=start_date,
        end_date=end_date,
        time=data.get('time'),
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
