"""Authenticated band availability endpoints, deliberately absent from v1."""
import json

from flask import Blueprint, current_app, jsonify, request

from auth_service import current_app_user, require_auth
from errors import ValidationError
import availability_service as service

availability_bp = Blueprint('availability', __name__, url_prefix='/api/v2/bands/<int:band_id>/availability')


@availability_bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Authorization')
    return response


@availability_bp.before_request
def schema_ready():
    if not current_app.config.get('BAND_API_SCHEMA_READY', False):
        return jsonify({'error': 'band_schema_unavailable'}), 503


def payload():
    if request.content_length is not None and request.content_length > 32768:
        raise ValidationError('Calendar request too large.', 413)
    raw = request.stream.read(32769)
    if len(raw) > 32768:
        raise ValidationError('Calendar request too large.', 413)
    try:
        def no_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=no_duplicates)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValidationError('A valid JSON object is required.')
    if not isinstance(value, dict):
        raise ValidationError('A JSON object is required.')
    return value


@availability_bp.get('')
@require_auth
def get_availability(band_id):
    if set(request.args) != {'from', 'to'} or any(len(request.args.getlist(k)) != 1 for k in request.args):
        raise ValidationError('from and to are required exactly once.')
    return jsonify(service.snapshot(current_app_user(), band_id, request.args['from'], request.args['to']))


@availability_bp.put('/me/batch')
@require_auth
def save_days(band_id):
    return jsonify(service.update_days(current_app_user(), band_id, payload()))


@availability_bp.delete('/me/<string:target_date>')
@require_auth
def clear_day(band_id, target_date):
    return jsonify(service.reset_day(current_app_user(), band_id, target_date, payload()))


@availability_bp.route('/me/notes/<string:target_date>/<string:target_period>', methods=['PUT', 'DELETE'])
@require_auth
def save_note(band_id, target_date, target_period):
    return jsonify(service.update_note(current_app_user(), band_id, target_date, target_period,
                                       payload(), delete=request.method == 'DELETE'))


@availability_bp.post('/reset')
@require_auth
def clear_band(band_id):
    return jsonify(service.reset_band(current_app_user(), band_id, payload()))
