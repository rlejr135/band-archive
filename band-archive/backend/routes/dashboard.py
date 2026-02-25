from flask import Blueprint, jsonify
from sqlalchemy import func

from extensions import db
from models import Song

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard/stats', methods=['GET'])
def get_stats():
    total_songs = db.session.query(func.count(Song.id)).scalar()

    status_rows = db.session.query(Song.status, func.count(Song.id)).group_by(Song.status).all()
    status_counts = {status: count for status, count in status_rows}

    return jsonify({
        "total_songs": total_songs,
        "status_counts": status_counts,
    })
