import math

from flask import Blueprint, jsonify, request

from extensions import db
from models import Comment, Media, PersonalLog
from errors import NotFoundError, ValidationError
from route_helpers import get_or_404

comments_bp = Blueprint('comments', __name__)


def _get_comment_or_404(id):
    return get_or_404(db.session, Comment, id, "Comment not found")


def _get_top_level_comments(query_filter):
    """Get top-level comments (no parent) with nested replies via to_dict()."""
    comments = Comment.query.filter(
        query_filter,
        Comment.parent_id.is_(None)
    ).order_by(Comment.created_at.asc()).all()
    return [c.to_dict() for c in comments]


def _parse_timestamp_seconds(data, *, allow_timestamp, target=None):
    """Return a validated optional playback offset from a comment payload.

    ``None`` is deliberately valid for compatibility and for clearing an
    existing link.  Only audio/video top-level targets may receive a numeric
    offset; replies intentionally cannot introduce a second, unrelated one.
    """
    if 'timestamp_seconds' not in data:
        return None

    value = data['timestamp_seconds']
    if value is None:
        return None
    if not allow_timestamp:
        raise ValidationError('Replies cannot have a timestamp')
    if target is None or target.file_type not in ('audio', 'video'):
        raise ValidationError('Timestamps are only available for audio or video comments')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError('timestamp_seconds must be a non-negative finite number')
    try:
        timestamp = float(value)
    except (OverflowError, ValueError):
        raise ValidationError('timestamp_seconds must be a non-negative finite number')
    if timestamp < 0 or not math.isfinite(timestamp):
        raise ValidationError('timestamp_seconds must be a non-negative finite number')
    return timestamp


def _comment_target(comment):
    if comment.media_id is not None:
        return comment.media
    if comment.personal_log_id is not None:
        return comment.personal_log
    return None


def _create_comment(data, media_id=None, personal_log_id=None, parent_id=None, target=None):
    """Shared comment creation logic."""
    if not data:
        raise ValidationError("Request body is required")

    author = data.get('author', '').strip()
    password = data.get('password', '').strip()
    content = data.get('content', '').strip()

    if not author:
        raise ValidationError("Author is required")
    if len(author) > 50:
        raise ValidationError("Author must be 50 characters or less")
    if not password:
        raise ValidationError("Password is required")
    if not content:
        raise ValidationError("Content is required")

    timestamp_seconds = _parse_timestamp_seconds(
        data,
        allow_timestamp=parent_id is None,
        target=target,
    )

    comment = Comment(
        media_id=media_id,
        personal_log_id=personal_log_id,
        parent_id=parent_id,
        author=author,
        content=content,
        timestamp_seconds=timestamp_seconds,
    )
    comment.set_password(password)
    db.session.add(comment)
    db.session.commit()
    return comment


# --- Media comments ---

@comments_bp.route('/media/<int:media_id>/comments', methods=['GET'])
def get_media_comments(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")
    return jsonify(_get_top_level_comments(Comment.media_id == media_id))


@comments_bp.route('/media/<int:media_id>/comments', methods=['POST'])
def create_media_comment(media_id):
    media = db.session.get(Media, media_id)
    if not media:
        raise NotFoundError("Media not found")
    comment = _create_comment(request.json, media_id=media_id, target=media)
    return jsonify(comment.to_dict()), 201


# --- PersonalLog comments ---

@comments_bp.route('/personal-logs/<int:log_id>/comments', methods=['GET'])
def get_personal_log_comments(log_id):
    log = db.session.get(PersonalLog, log_id)
    if not log:
        raise NotFoundError("Personal log not found")
    return jsonify(_get_top_level_comments(Comment.personal_log_id == log_id))


@comments_bp.route('/personal-logs/<int:log_id>/comments', methods=['POST'])
def create_personal_log_comment(log_id):
    log = db.session.get(PersonalLog, log_id)
    if not log:
        raise NotFoundError("Personal log not found")
    comment = _create_comment(request.json, personal_log_id=log_id, target=log)
    return jsonify(comment.to_dict()), 201


# --- Replies ---

@comments_bp.route('/comments/<int:comment_id>/replies', methods=['POST'])
def create_reply(comment_id):
    parent = _get_comment_or_404(comment_id)
    comment = _create_comment(
        request.json,
        media_id=parent.media_id,
        personal_log_id=parent.personal_log_id,
        parent_id=parent.id,
        target=_comment_target(parent),
    )
    return jsonify(comment.to_dict()), 201


# --- Update / Delete ---

@comments_bp.route('/comments/<int:comment_id>', methods=['PUT'])
def update_comment(comment_id):
    comment = _get_comment_or_404(comment_id)
    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    password = data.get('password', '').strip()
    if not password or not comment.check_password(password):
        raise ValidationError("Invalid password")

    content = data.get('content', '').strip()
    if not content:
        raise ValidationError("Content is required")

    timestamp_supplied = 'timestamp_seconds' in data
    timestamp_seconds = _parse_timestamp_seconds(
        data,
        allow_timestamp=comment.parent_id is None,
        target=_comment_target(comment),
    ) if timestamp_supplied else None

    comment.content = content
    if timestamp_supplied:
        comment.timestamp_seconds = timestamp_seconds
    db.session.commit()
    return jsonify(comment.to_dict())


@comments_bp.route('/comments/<int:comment_id>', methods=['DELETE'])
def delete_comment(comment_id):
    comment = _get_comment_or_404(comment_id)
    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    password = data.get('password', '').strip()
    if not password or not comment.check_password(password):
        raise ValidationError("Invalid password")

    db.session.delete(comment)
    db.session.commit()
    return jsonify({"message": "Comment deleted"})
