"""Own profile editing, never manager editing another person's biography."""
from flask import Blueprint, current_app, jsonify
from auth_service import current_app_user, require_auth
from member_profile_service import own_profile
from http_request_json import payload

member_profiles_bp = Blueprint('member_profiles', __name__,
                               url_prefix='/api/v2/bands/<int:band_id>/members')


@member_profiles_bp.before_request
def schema_ready():
    if not current_app.config.get('BAND_API_SCHEMA_READY', False):
        return jsonify({'error': 'band_schema_unavailable'}), 503


@member_profiles_bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Authorization')
    return response


@member_profiles_bp.get('/me')
@require_auth
def get_own_profile(band_id):
    return jsonify({'member': own_profile(current_app_user(), band_id)})


@member_profiles_bp.patch('/me')
@require_auth
def patch_own_profile(band_id):
    return jsonify({'member': own_profile(current_app_user(), band_id, payload())})
