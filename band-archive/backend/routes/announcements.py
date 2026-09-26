from flask import Blueprint, jsonify, request

from extensions import db
from models import Announcement
from errors import NotFoundError, ValidationError
from legacy_scope import legacy_band_id, legacy_query

announcements_bp = Blueprint('announcements', __name__)


@announcements_bp.route('/announcement', methods=['GET'])
def get_announcement():
    announcement = legacy_query(Announcement).one_or_none()
    if not announcement:
        return jsonify({'id': None, 'content': '', 'updated_at': None})
    return jsonify(announcement.to_dict())


@announcements_bp.route('/announcement', methods=['PUT'])
def update_announcement():
    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    content = data.get('content', '').strip()
    if not content:
        raise ValidationError("Content is required")

    band_id = legacy_band_id()
    announcement = legacy_query(Announcement).one_or_none()
    if announcement:
        announcement.content = content
    else:
        announcement = Announcement(band_id=band_id, content=content)
        db.session.add(announcement)

    db.session.commit()
    return jsonify(announcement.to_dict())
