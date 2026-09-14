from flask import Blueprint, jsonify

from auth_service import current_app_user, require_auth


auth_bp = Blueprint('auth', __name__, url_prefix='/api/v2')


@auth_bp.get('/me')
@require_auth
def me():
    return jsonify({'user': current_app_user().to_dict()})
