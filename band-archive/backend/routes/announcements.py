from flask import Blueprint, jsonify, request

from extensions import db
from models import Announcement
from errors import NotFoundError, ValidationError

announcements_bp = Blueprint('announcements', __name__)


@announcements_bp.route('/announcement', methods=['GET'])
def get_announcement():
    announcement = db.session.get(Announcement, 1)
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

    announcement = db.session.get(Announcement, 1)
    if announcement:
        announcement.content = content
    else:
        announcement = Announcement(id=1, content=content)
        db.session.add(announcement)

    db.session.commit()
    return jsonify(announcement.to_dict())
