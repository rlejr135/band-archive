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


def _create_comment(data, media_id=None, personal_log_id=None, parent_id=None):
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

    comment = Comment(
        media_id=media_id,
        personal_log_id=personal_log_id,
        parent_id=parent_id,
        author=author,
        content=content,
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
    comment = _create_comment(request.json, media_id=media_id)
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
    comment = _create_comment(request.json, personal_log_id=log_id)
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

    comment.content = content
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
