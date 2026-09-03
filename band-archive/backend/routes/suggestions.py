from flask import Blueprint, jsonify, request

from extensions import db
from models import Song, SongSuggestion
from errors import NotFoundError, ValidationError
from route_helpers import get_or_404
from validators import validate_string_length

suggestions_bp = Blueprint('suggestions', __name__)


def _get_suggestion_or_404(id):
    return get_or_404(db.session, SongSuggestion, id, "Suggestion not found")


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
    validate_string_length(title, 'title', 100)
    if not artist:
        raise ValidationError("Artist is required")
    validate_string_length(artist, 'artist', 100)
    if not link:
        raise ValidationError("Link is required")
    validate_string_length(link, 'link', 500)

    memo = data.get('memo', '').strip() or None
    suggestion = SongSuggestion(title=title, artist=artist, link=link, memo=memo)
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


@suggestions_bp.route('/suggestions/<int:id>/promote', methods=['POST'])
def promote_suggestion(id):
    """Move one approved suggestion into the song list atomically."""
    suggestion = _get_suggestion_or_404(id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get('password') != 'admin':
        raise ValidationError("Invalid password")

    song = Song(
        title=suggestion.title,
        artist=suggestion.artist,
        link=suggestion.link,
        memo=suggestion.memo,
        status='Practice',
    )
    try:
        db.session.add(song)
        db.session.delete(suggestion)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return jsonify({'song': song.to_dict()}), 201


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
