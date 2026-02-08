from flask import Blueprint, jsonify, request

from extensions import db
from models import SongSuggestion
from errors import NotFoundError, ValidationError

suggestions_bp = Blueprint('suggestions', __name__)


def _get_suggestion_or_404(id):
    suggestion = db.session.get(SongSuggestion, id)
    if not suggestion:
        raise NotFoundError("Suggestion not found")
    return suggestion


@suggestions_bp.route('/suggestions', methods=['GET'])
def get_suggestions():
    suggestions = SongSuggestion.query.order_by(
        (SongSuggestion.thumbs_up - SongSuggestion.thumbs_down).desc()
    ).all()
    return jsonify([s.to_dict() for s in suggestions])


@suggestions_bp.route('/suggestions', methods=['POST'])
def create_suggestion():
    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    title = data.get('title', '').strip()
    artist = data.get('artist', '').strip()
    link = data.get('link', '').strip()

    if not title:
        raise ValidationError("Title is required")
    if not artist:
        raise ValidationError("Artist is required")
    if not link:
        raise ValidationError("Link is required")

    suggestion = SongSuggestion(title=title, artist=artist, link=link)
    db.session.add(suggestion)
    db.session.commit()
    return jsonify(suggestion.to_dict()), 201


@suggestions_bp.route('/suggestions/<int:id>', methods=['DELETE'])
def delete_suggestion(id):
    suggestion = _get_suggestion_or_404(id)

    data = request.json
    if not data or data.get('password') != 'admin':
        raise ValidationError("Invalid password")

    db.session.delete(suggestion)
    db.session.commit()
    return jsonify({"message": "Suggestion deleted"}), 200


@suggestions_bp.route('/suggestions/<int:id>/vote', methods=['POST'])
def vote_suggestion(id):
    suggestion = _get_suggestion_or_404(id)

    data = request.json
    if not data:
        raise ValidationError("Request body is required")

    vote_type = data.get('vote_type')
    if vote_type == 'up':
        suggestion.thumbs_up += 1
    elif vote_type == 'down':
        suggestion.thumbs_down += 1
    else:
        raise ValidationError("vote_type must be 'up' or 'down'")

    db.session.commit()
    return jsonify(suggestion.to_dict())
