from datetime import timezone
import json

from flask import Blueprint, current_app, jsonify, request

from auth_service import current_app_user, require_auth
from errors import ValidationError
from policy_service import accept_policy, public_policy_config, receipt_required


auth_bp = Blueprint('auth', __name__, url_prefix='/api/v2')


@auth_bp.get('/me')
@require_auth
def me():
    user = current_app_user()
    payload = user.to_dict()
    if current_app.config.get('BAND_API_SCHEMA_READY', False):
        payload['policy_acceptance_required'] = receipt_required(user)
    else:
        payload['policy_acceptance_required'] = False
    return jsonify({'user': payload})


@auth_bp.get('/policy')
def policy_config():
    response = jsonify(public_policy_config())
    response.headers['Cache-Control'] = 'no-store'
    return response


@auth_bp.post('/me/policy-acceptance')
@require_auth
def policy_acceptance():
    if not current_app.config.get('BAND_API_SCHEMA_READY', False):
        return jsonify({'error': 'policy_schema_unavailable'}), 503
    if request.content_length is not None and request.content_length > 4096:
        return jsonify({'error': 'request_too_large'}), 413
    raw_body = request.stream.read(4097)
    if len(raw_body) > 4096:
        return jsonify({'error': 'request_too_large'}), 413
    try:
        body = json.loads(raw_body) if raw_body else None
    except (TypeError, ValueError):
        body = None
    if not isinstance(body, dict):
        raise ValidationError('A JSON object is required.')
    item, created = accept_policy(current_app_user(), body)
    accepted_at = item.accepted_at if item else None
    if accepted_at is not None and accepted_at.tzinfo is None:
        accepted_at = accepted_at.replace(tzinfo=timezone.utc)
    response = jsonify({
        'accepted': item is not None,
        'created': created,
        'accepted_at': accepted_at.astimezone(timezone.utc).isoformat() if accepted_at else None,
    })
    response.headers['Cache-Control'] = 'no-store'
    return response, 201 if created else 200
