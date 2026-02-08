from flask import Blueprint, jsonify
from sqlalchemy import func

from extensions import db
from models import Song, PracticeLog

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard/stats', methods=['GET'])
def get_stats():
    total_songs = db.session.query(func.count(Song.id)).scalar()

    status_rows = db.session.query(Song.status, func.count(Song.id)).group_by(Song.status).all()
    status_counts = {status: count for status, count in status_rows}

    total_practice_logs = db.session.query(func.count(PracticeLog.id)).scalar()

    recent_logs = (
        PracticeLog.query
        .order_by(PracticeLog.date.desc())
        .limit(5)
        .all()
    )

    return jsonify({
        "total_songs": total_songs,
        "status_counts": status_counts,
        "recent_practice_logs": [log.to_dict() for log in recent_logs],
        "total_practice_logs": total_practice_logs,
    })
