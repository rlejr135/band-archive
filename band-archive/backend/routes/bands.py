"""Phase 3 API: private membership management plus public band discovery."""

from flask import Blueprint, current_app, jsonify, request

from auth_service import current_app_user, require_auth
from band_service import (
    ACTIVE, MANAGER_ROLES, approve_membership, band_dict, close_band, create_invite,
    create_request, creation_request_dict, invite_dict, join_invite, join_request,
    leave_band, membership_dict, require_band_role, review_request, revoke_invite,
    set_role, transfer_ownership,
)
from errors import NotFoundError, ValidationError
from extensions import db
from models import Band, BandCreationRequest, BandInviteCode, BandMembership


bands_bp = Blueprint('bands', __name__, url_prefix='/api/v2')


@bands_bp.before_request
def require_band_schema():
    """Keep the v1 compatibility app usable before the Phase 3 upgrade.

    Blueprint registration is harmless, but querying an absent Alembic table
    is not.  Operators receive a deterministic readiness response and can use
    the documented migration runner before enabling these routes.
    """
    if not current_app.config.get('BAND_API_SCHEMA_READY', False):
        return jsonify({
            'error': 'band_schema_unavailable',
            'message': 'Band API requires the isolated Alembic migration before startup.',
        }), 503


def payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValidationError('A JSON object is required.')
    return value


def require_platform_admin():
    user = current_app_user()
    if not user.is_platform_admin:
        raise ValidationError('Platform administrator permission is required.', 403)
    return user


@bands_bp.get('/bands')
@require_auth
def my_bands():
    user = current_app_user()
    memberships = BandMembership.query.filter_by(user_id=user.id, status=ACTIVE).join(Band).filter(
        Band.deleted_at.is_(None)).order_by(Band.name, Band.id).all()
    return jsonify({'bands': [band_dict(item.band, user) for item in memberships]})


@bands_bp.get('/public/bands')
def public_bands():
    search = (request.args.get('q') or '').strip()
    query = Band.query.filter_by(default_visibility='PUBLIC').filter(Band.deleted_at.is_(None))
    if search:
        escaped = search.replace('%', '\\%').replace('_', '\\_')
        query = query.filter((Band.name.ilike(f'%{escaped}%')) | (Band.slug.ilike(f'%{escaped}%')))
    bands = query.order_by(Band.name, Band.id).limit(100).all()
    return jsonify({'bands': [band_dict(band, include_roster=False) for band in bands]})


@bands_bp.get('/bands/by-slug/<string:band_slug>')
def get_band_by_slug(band_slug):
    band = Band.query.filter_by(slug=band_slug.strip().casefold(), deleted_at=None).one_or_none()
    if band is None or band.default_visibility != 'PUBLIC':
        raise NotFoundError('Band not found')
    # This route is deliberately anonymous; authenticated callers use /bands
    # for their private memberships and receive explicit permission booleans.
    return jsonify({'band': band_dict(band, include_roster=True)})


@bands_bp.post('/band-creation-requests')
@require_auth
def request_band_creation():
    item, created = create_request(current_app_user(), payload())
    return jsonify({'request': creation_request_dict(item), 'created': created}), 201 if created else 200


@bands_bp.get('/platform/band-creation-requests')
@require_auth
def list_creation_requests():
    require_platform_admin()
    status = request.args.get('status')
    query = BandCreationRequest.query
    if status:
        if status not in {'pending', 'active', 'rejected'}:
            raise ValidationError('Invalid request status.')
        query = query.filter_by(status=status)
    return jsonify({'requests': [creation_request_dict(item) for item in query.order_by(BandCreationRequest.id).all()]})


@bands_bp.patch('/platform/band-creation-requests/<int:request_id>')
@require_auth
def review_creation_request(request_id):
    admin = require_platform_admin()
    body = payload()
    action = body.get('action')
    if action not in {'approve', 'reject'}:
        raise ValidationError('action must be approve or reject.')
    item, changed = review_request(admin, request_id, action == 'approve', body.get('review_note'))
    return jsonify({'request': creation_request_dict(item), 'changed': changed})


@bands_bp.post('/platform/band-creation-requests/<int:request_id>/approve')
@require_auth
def approve_creation_request(request_id):
    item, changed = review_request(require_platform_admin(), request_id, True)
    return jsonify({'request': creation_request_dict(item), 'changed': changed})


@bands_bp.post('/platform/band-creation-requests/<int:request_id>/reject')
@require_auth
def reject_creation_request(request_id):
    item, changed = review_request(require_platform_admin(), request_id, False)
    return jsonify({'request': creation_request_dict(item), 'changed': changed})


@bands_bp.post('/bands/<int:band_id>/join-requests')
@require_auth
def request_join(band_id):
    item, created = join_request(current_app_user(), band_id)
    return jsonify({'membership': membership_dict(item), 'created': created}), 201 if created else 200


@bands_bp.patch('/memberships/<int:membership_id>')
@require_auth
def change_membership(membership_id):
    actor = current_app_user()
    body = payload()
    action = body.get('action')
    if action in {'approve', 'reject'}:
        item, changed = approve_membership(actor, membership_id, action == 'approve')
        return jsonify({'membership': membership_dict(item), 'changed': changed})
    if 'role' in body:
        item = set_role(actor, membership_id, body['role'])
        return jsonify({'membership': membership_dict(item)})
    raise ValidationError('Provide action approve/reject or role.')


@bands_bp.get('/bands/<int:band_id>/memberships')
@require_auth
def list_memberships(band_id):
    user = current_app_user()
    band, membership = require_band_role(user, band_id, roles={'owner', 'admin', 'member'})
    # Members may see only the active roster (when one is otherwise hidden);
    # pending and historical entries remain a management concern.
    query = BandMembership.query.filter_by(band_id=band.id)
    if membership.role not in MANAGER_ROLES:
        query = query.filter_by(status=ACTIVE)
    audience = 'management' if membership.role in MANAGER_ROLES else 'member'
    return jsonify({'memberships': [membership_dict(item, audience=audience)
                                    for item in query.order_by(BandMembership.id).all()]})


@bands_bp.post('/bands/<int:band_id>/invite-codes')
@require_auth
def issue_invite(band_id):
    invite, code = create_invite(current_app_user(), band_id)
    # The raw capability is intentionally returned in this one response only.
    return jsonify({'invite': invite_dict(invite), 'code': code}), 201


@bands_bp.get('/bands/<int:band_id>/invite-codes')
@require_auth
def list_invites(band_id):
    band, _ = require_band_role(current_app_user(), band_id)
    invites = BandInviteCode.query.filter_by(band_id=band.id).order_by(BandInviteCode.id).all()
    return jsonify({'invite_codes': [invite_dict(invite) for invite in invites]})


@bands_bp.post('/bands/<int:band_id>/invite-codes/<int:invite_id>/revoke')
@require_auth
def revoke_invite_code(band_id, invite_id):
    return jsonify({'invite': invite_dict(revoke_invite(current_app_user(), band_id, invite_id))})


@bands_bp.post('/bands/<int:band_id>/invite-codes/<int:invite_id>/rotate')
@require_auth
def rotate_invite_code(band_id, invite_id):
    actor = current_app_user()
    revoke_invite(actor, band_id, invite_id)
    invite, code = create_invite(actor, band_id)
    return jsonify({'invite': invite_dict(invite), 'code': code}), 201


@bands_bp.post('/invite-codes/join')
@require_auth
def redeem_invite():
    # Capabilities must not appear in URLs: reverse proxies, browser history,
    # and access logs commonly retain paths and query strings.
    code = payload().get('code')
    item = join_invite(current_app_user(), code)
    return jsonify({'membership': membership_dict(item)})


@bands_bp.post('/bands/<int:band_id>/ownership-transfer')
@require_auth
def transfer_band_ownership(band_id):
    body = payload()
    target_id = body.get('membership_id')
    if not isinstance(target_id, int):
        raise ValidationError('membership_id must be an integer.')
    item = transfer_ownership(current_app_user(), band_id, target_id)
    return jsonify({'membership': membership_dict(item)})


@bands_bp.post('/bands/<int:band_id>/leave')
@require_auth
def leave(band_id):
    return jsonify({'membership': membership_dict(leave_band(current_app_user(), band_id))})


@bands_bp.delete('/bands/<int:band_id>')
@require_auth
def close(band_id):
    band = close_band(current_app_user(), band_id)
    return jsonify({'band': band_dict(band, current_app_user())})
