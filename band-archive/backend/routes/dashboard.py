from flask import Blueprint, jsonify
from sqlalchemy import func

from extensions import db
from models import Song
from legacy_scope import legacy_band_id
from errors import NotFoundError
from legacy_read_visibility import legacy_read_scope
from band_access import PUBLIC
from content_restrictions import restriction_predicate

dashboard_bp = Blueprint('dashboard', __name__)


def _empty_stats():
    response = jsonify({"total_songs": 0, "status_counts": {}})
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Authorization')
    return response


@dashboard_bp.route('/dashboard/stats', methods=['GET'])
def get_stats():
    band_id = legacy_band_id()
    try:
        scope = legacy_read_scope(band_id)
    except NotFoundError:
        # Preserve the legacy response shape without revealing whether a
        # private import band contains any content.
        return _empty_stats()

    visible_songs = db.session.query(Song.id, Song.status).filter(
        Song.band_id == band_id, restriction_predicate(Song),
    )
    if not scope.is_member:
        if scope.band_visibility != PUBLIC:
            return _empty_stats()
        visible_songs = visible_songs.filter(Song.visibility == PUBLIC)

    total_songs = visible_songs.with_entities(func.count(Song.id)).scalar()

    status_rows = visible_songs.with_entities(Song.status, func.count(Song.id)).group_by(Song.status).all()
    status_counts = {status: count for status, count in status_rows}

    return jsonify({
        "total_songs": total_songs,
        "status_counts": status_counts,
    })
