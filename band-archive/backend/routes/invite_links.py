"""Explicit, rate-limited API for reusable invitation capabilities."""
import json
from flask import Blueprint, current_app, jsonify, request

from auth_service import current_app_user, require_auth
from band_access import current_audience
from errors import NotFoundError, ValidationError
from extensions import db
from invite_introduction import introduction_payload
from invite_link_service import (RateLimited, accept, create_link, create_resume_context,
                                 link_metadata, list_links, rate_limit, resume, revoke_link, utc_iso)
from models import Band

invite_links_bp = Blueprint('invite_links', __name__, url_prefix='/api/v2')


@invite_links_bp.before_request
def require_invite_schema():
    if current_app.config.get('BAND_API_SCHEMA_READY', False):
        return None
    response = jsonify({'error': 'invite_schema_unavailable',
                        'message': 'Reusable invitations require the current database migration.'})
    response.status_code = 503
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Authorization')
    return response


def _body(allowed, optional=()):
    if request.content_length is not None and request.content_length > 8192:
        raise ValidationError('Request body is too large.', 413)
    raw = request.stream.read(8193)
    if len(raw) > 8192:
        raise ValidationError('Request body is too large.', 413)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ValidationError('A valid JSON object is required.')
    if (not isinstance(value, dict) or not set(allowed) <= set(value)
            or set(value) - set(allowed) - set(optional)):
        raise ValidationError('Unexpected request fields.')
    return value


def _limited(call):
    try:
        return call()
    except RateLimited as error:
        response = jsonify({'error': 'rate_limited', 'message': str(error)})
        response.status_code = 429
        response.headers['Retry-After'] = str(error.retry_after)
        return response


def _check_limit(call):
    try:
        call()
    except RateLimited as error:
        response = jsonify({'error': 'rate_limited', 'message': str(error)})
        response.status_code = 429
        response.headers['Retry-After'] = str(error.retry_after)
        return response
    return None


@invite_links_bp.post('/invite-links/preview')
def preview_invite():
    from invite_link_service import _live_link_by_token
    # Validate any supplied Authorization header. Invalid credentials are never
    # silently treated as anonymous; membership state is still viewer-only.
    audience = current_audience()
    limited = _check_limit(lambda: rate_limit('invite-preview', 20, 60))
    if limited is not None:
        return limited
    body = _body({'token'})
    pair = _live_link_by_token(body['token'])
    if pair is None:
        raise NotFoundError('Invitation not found')
    link, band = pair
    introduction = introduction_payload(band, audience=audience)
    viewer = introduction.pop('viewer')
    return jsonify({'invite': {
        'id': link.id,
        'expires_at': utc_iso(link.expires_at),
        **introduction,
    }, 'viewer': viewer})


@invite_links_bp.post('/invite-links/resume-context')
def start_resume_context():
    from invite_link_service import request_address_hash
    # Even though this endpoint is policy-exempt, an explicit bad token must
    # not become anonymous. Authentication does not influence the binding.
    current_audience()
    body = _body({'token', 'email'})
    try:
        context, expires_at = create_resume_context(body['token'], body['email'], request_address_hash())
    except RateLimited as error:
        response = jsonify({'error': 'rate_limited', 'message': str(error)})
        response.status_code = 429
        response.headers['Retry-After'] = str(error.retry_after)
        return response
    return jsonify({'context': context, 'expires_at': utc_iso(expires_at)}), 201


@invite_links_bp.post('/invite-links/resume')
@require_auth
def resume_invite():
    body = _body({'context'})
    actor = current_app_user(required=True)
    return _limited(lambda: jsonify(resume(body['context'], actor)))


@invite_links_bp.post('/invite-links/accept')
@require_auth
def accept_invite():
    body = _body({'context'}, optional={'name'})
    actor = current_app_user(required=True)
    return _limited(lambda: jsonify(accept(body['context'], actor, name=body.get('name'))))


@invite_links_bp.get('/bands/<int:band_id>/invite-links')
@require_auth
def get_invite_links(band_id):
    actor = current_app_user(required=True)
    try:
        limit, offset = int(request.args.get('limit', '20')), int(request.args.get('offset', '0'))
    except ValueError:
        raise ValidationError('limit and offset must be integers.')
    if not 1 <= limit <= 100 or offset < 0 or offset > 100_000:
        raise ValidationError('Invalid pagination.')
    limited = _check_limit(lambda: rate_limit('invite-manager', 10, 60, actor=actor))
    if limited is not None:
        return limited
    rows, pagination = list_links(actor, band_id, limit, offset)
    band = db.session.get(Band, band_id)
    return jsonify({'band': {'id': band.id, 'name': band.name, 'slug': band.slug},
                    'invite_links': [link_metadata(item) for item in rows], 'pagination': pagination})


@invite_links_bp.post('/bands/<int:band_id>/invite-links')
@require_auth
def post_invite_link(band_id):
    actor = current_app_user(required=True)
    _body(set())
    limited = _check_limit(lambda: rate_limit('invite-manager', 10, 60, actor=actor))
    if limited is not None:
        return limited
    item, token = create_link(actor, band_id)
    return jsonify({'invite_link': link_metadata(item), 'token': token}), 201


@invite_links_bp.post('/bands/<int:band_id>/invite-links/<int:link_id>/revoke')
@require_auth
def post_revoke_invite_link(band_id, link_id):
    actor = current_app_user(required=True)
    _body(set())
    limited = _check_limit(lambda: rate_limit('invite-manager', 10, 60, actor=actor))
    if limited is not None:
        return limited
    return jsonify({'invite_link': link_metadata(revoke_link(actor, band_id, link_id))})
