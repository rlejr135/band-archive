"""Narrow, token-gated inventory endpoints for offline migration workers."""

import hmac

from flask import Blueprint, current_app, jsonify, request

from models import Media


migrations_bp = Blueprint('migrations', __name__)


@migrations_bp.route('/internal/migrations/r2-720p/inventory', methods=['GET'])
def r2_720p_inventory():
    configured = current_app.config.get('R2_MIGRATION_TOKEN')
    if not configured:
        # Do not advertise an operational migration surface when disabled.
        return '', 404
    supplied = request.headers.get('X-Migration-Token', '')
    if not hmac.compare_digest(str(configured), supplied):
        return '', 403
    records = Media.query.order_by(Media.id.asc()).all()
    return jsonify([{
        'id': media.id,
        'storage_filename': media.filename,
        'file_type': media.file_type,
        'video_720_filename': media.video_720_filename,
        'video_720_source_etag': media.video_720_source_etag,
        'video_720_profile': media.video_720_profile,
    } for media in records])
