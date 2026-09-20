"""Authorization and transactional operations for the Phase 3 band boundary."""

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from errors import NotFoundError, ValidationError
from extensions import db
from models import AuditEvent, Band, BandCreationRequest, BandInviteCode, BandMembership


OWNER_ROLES = {'owner'}
MANAGER_ROLES = {'owner', 'admin'}
ACTIVE = 'active'
_SLUG = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_UNRESOLVED_MEMBERSHIP = object()


def now():
    return datetime.now(timezone.utc)


def _begin_privileged_mutation():
    """Claim SQLite's writer lock before checking authority.

    ``SELECT .. FOR UPDATE`` is intentionally a no-op on SQLite.  Starting an
    IMMEDIATE transaction before an authorization read makes the read and its
    subsequent mutation one serialization point, matching PostgreSQL's row
    locks.  Calling this after Flask has loaded the current user is safe: the
    SQLite driver has not acquired a writer lock for that read.
    """
    connection = db.session.connection()
    if connection.dialect.name == 'sqlite':
        connection.exec_driver_sql('BEGIN IMMEDIATE')


def slug(value):
    value = value.strip().casefold() if isinstance(value, str) else ''
    if not _SLUG.fullmatch(value) or len(value) > 80:
        raise ValidationError('slug must use lowercase letters, numbers, and single hyphens.')
    return value


def required_text(payload, key, maximum):
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValidationError(f'{key} is required and must be at most {maximum} characters.')
    return value.strip()


def optional_text(payload, key):
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f'{key} must be text.')
    return value.strip() or None


def audit(event_type, actor=None, band=None, target=None, details=None):
    """Append a deliberately token-free audit record in the current transaction."""
    db.session.add(AuditEvent(
        band_id=band.id if band else None,
        actor_user_id=actor.id if actor else None,
        event_type=event_type,
        target_type=target.__class__.__name__ if target else None,
        target_id=str(target.id) if target and target.id is not None else None,
        details_json=json.dumps(details, sort_keys=True) if details else None,
    ))


def active_membership(user, band_id, include_deleted=False):
    band = Band.query.filter_by(id=band_id).populate_existing().one_or_none()
    if band is None or (band.deleted_at is not None and not include_deleted):
        raise NotFoundError('Band not found')
    membership = BandMembership.query.filter_by(
        band_id=band.id, user_id=user.id, status=ACTIVE,
    ).populate_existing().one_or_none()
    return band, membership


def require_band_role(user, band_id, roles=MANAGER_ROLES, lock=False):
    """Authorize a band action, optionally locking the actor's authority.

    Mutations pass ``lock=True`` so PostgreSQL rechecks the role under the
    same row lock as the write. SQLite's writer transaction/constraints are
    the corresponding portable protection.
    """
    band_query = Band.query.filter_by(id=band_id)
    if lock:
        band_query = band_query.with_for_update().populate_existing()
    band = band_query.one_or_none()
    if band is None or band.deleted_at is not None:
        raise NotFoundError('Band not found')
    membership_query = BandMembership.query.filter_by(band_id=band.id, user_id=user.id, status=ACTIVE)
    if lock:
        membership_query = membership_query.with_for_update().populate_existing()
    membership = membership_query.one_or_none()
    if membership is None:
        # Do not disclose a band to a non-member through protected endpoints.
        raise NotFoundError('Band not found')
    if membership.role not in roles:
        raise ValidationError('Insufficient band role.', 403)
    return band, membership


def membership_dict(membership, audience='member'):
    """Serialize a membership for the least-privileged caller.

    Roster consumers never need account state.  Band managers do need an
    email and lifecycle dates to administer requests, but even they must not
    receive global platform-admin or email-verification attributes.
    """
    user = None
    member_user = getattr(membership, 'user', None)
    if member_user:
        user = {'id': member_user.supabase_user_id, 'display_name': member_user.display_name}
        if audience == 'management':
            user['email'] = member_user.email
    elif getattr(membership, 'user_subject', None):
        # Phase 6 read scopes carry an immutable active-membership snapshot.
        # It avoids a second, unconstrained membership lookup during response
        # serialization while retaining the established wire format.
        user = {'id': membership.user_subject, 'display_name': membership.user_display_name}
        if audience == 'management':
            user['email'] = membership.user_email
    result = {
        'id': membership.id,
        'band_id': membership.band_id,
        'user': user,
        'role': membership.role,
    }
    if audience == 'management':
        result.update({
            'status': membership.status,
            'requested_at': membership.requested_at.isoformat() if membership.requested_at else None,
            'approved_at': membership.approved_at.isoformat() if membership.approved_at else None,
            'left_at': membership.left_at.isoformat() if membership.left_at else None,
        })
    return result


def band_dict(band, viewer=None, include_roster=False, membership=_UNRESOLVED_MEMBERSHIP):
    if membership is _UNRESOLVED_MEMBERSHIP:
        membership = None
    if membership is None and viewer is not None:
        membership = BandMembership.query.filter_by(band_id=band.id, user_id=viewer.id).one_or_none()
    active_member = membership is not None and membership.status == ACTIVE
    response = {
        'id': band.id,
        'name': band.name,
        'slug': band.slug,
        'description': band.description,
        'default_visibility': band.default_visibility,
        'member_roster_public': band.member_roster_public,
        'visibility': band.default_visibility,
        'created_at': band.created_at.isoformat() if band.created_at else None,
        'permissions': {
            'can_view_roster': bool(band.member_roster_public or active_member),
            'can_manage_members': bool(active_member and membership.role in MANAGER_ROLES),
            'can_manage_invites': bool(active_member and membership.role in MANAGER_ROLES),
            'can_close_band': bool(active_member and membership.role == 'owner'),
        },
        'can_edit': bool(active_member and membership.role in MANAGER_ROLES),
        'can_delete': bool(active_member and membership.role == 'owner'),
        'can_manage': bool(active_member and membership.role in MANAGER_ROLES),
        'membership': membership_dict(membership) if membership else None,
    }
    if active_member and membership.role in MANAGER_ROLES:
        response['storage_quota_bytes'] = band.storage_quota_bytes
    if include_roster and (band.member_roster_public or active_member):
        response['members'] = [membership_dict(item, audience='member') for item in BandMembership.query.filter_by(
            band_id=band.id, status=ACTIVE).order_by(BandMembership.id).all()]
    return response


def creation_request_dict(item):
    return {
        'id': item.id, 'name': item.name, 'slug': item.slug, 'description': item.description,
        'status': item.status, 'band_id': item.band_id,
        'created_at': item.created_at.isoformat() if item.created_at else None,
        'reviewed_at': item.reviewed_at.isoformat() if item.reviewed_at else None,
        'review_note': item.review_note,
    }


def create_request(user, payload):
    name = required_text(payload, 'name', 120)
    requested_slug = slug(payload.get('slug', ''))
    description = optional_text(payload, 'description')
    existing = BandCreationRequest.query.filter_by(
        requested_by_user_id=user.id, slug=requested_slug, status='pending').one_or_none()
    if existing:
        return existing, False
    item = BandCreationRequest(requested_by_user_id=user.id, name=name, slug=requested_slug,
                               description=description)
    db.session.add(item)
    try:
        db.session.flush()
        audit('band_creation_requested', user, target=item, details={'slug': requested_slug})
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        existing = BandCreationRequest.query.filter_by(
            requested_by_user_id=user.id, slug=requested_slug, status='pending').one_or_none()
        if existing:
            return existing, False
        raise ValidationError('Unable to create band request.', 409) from error
    return item, True


def review_request(admin, request_id, approved, note=None):
    # PostgreSQL serializes concurrent reviewers.  SQLite accepts the clause
    # as a no-op and relies on its writer lock plus unique constraints.
    _begin_privileged_mutation()
    item = BandCreationRequest.query.filter_by(id=request_id).with_for_update().populate_existing().one_or_none()
    if item is None:
        raise NotFoundError('Band creation request not found')
    if item.status != 'pending':
        return item, False
    timestamp = now()
    item.reviewed_by_user_id = admin.id
    item.reviewed_at = timestamp
    item.review_note = note
    try:
        if not approved:
            item.status = 'rejected'
            audit('band_creation_rejected', admin, target=item)
            db.session.commit()
            return item, True
        if Band.query.filter_by(slug=item.slug).with_for_update().first() is not None:
            db.session.rollback()
            raise ValidationError('Band slug is already in use.', 409)
        band = Band(name=item.name, slug=item.slug, description=item.description,
                    created_by_user_id=item.requested_by_user_id)
        db.session.add(band)
        db.session.flush()
        owner = BandMembership(band_id=band.id, user_id=item.requested_by_user_id, role='owner', status=ACTIVE,
                               approved_at=timestamp, acted_by_user_id=admin.id)
        db.session.add(owner)
        # Flush before auditing so target_id is the persisted membership ID.
        db.session.flush()
        item.status = 'active'
        item.band_id = band.id
        audit('band_created', admin, band=band, target=item, details={'owner_user_id': owner.user_id})
        audit('membership_approved', admin, band=band, target=owner, details={'role': 'owner'})
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        # A concurrent reviewer either completed the request or won the slug.
        item = BandCreationRequest.query.filter_by(id=request_id).one_or_none()
        if item is not None and item.status != 'pending':
            return item, False
        raise ValidationError('Band creation could not be approved.', 409) from error
    return item, True


def join_request(user, band_id):
    band = Band.query.filter_by(id=band_id, deleted_at=None).one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    item = BandMembership.query.filter_by(band_id=band.id, user_id=user.id).one_or_none()
    if item is not None:
        return item, False
    item = BandMembership(band_id=band.id, user_id=user.id)
    db.session.add(item)
    try:
        db.session.flush()
        audit('membership_requested', user, band=band, target=item)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        item = BandMembership.query.filter_by(band_id=band.id, user_id=user.id).one()
        return item, False
    return item, True


def approve_membership(actor, membership_id, approved):
    _begin_privileged_mutation()
    item = BandMembership.query.filter_by(id=membership_id).with_for_update().populate_existing().one_or_none()
    if item is None:
        raise NotFoundError('Membership not found')
    band, _ = require_band_role(actor, item.band_id, lock=True)
    if item.status != 'pending':
        return item, False
    try:
        item.status = ACTIVE if approved else 'rejected'
        item.approved_at = now() if approved else None
        item.acted_by_user_id = actor.id
        audit('membership_approved' if approved else 'membership_rejected', actor, band=band, target=item)
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        item = BandMembership.query.filter_by(id=membership_id).one_or_none()
        if item is not None and item.status != 'pending':
            return item, False
        raise ValidationError('Membership decision could not be saved.', 409) from error
    return item, True


def _token_hash(raw):
    return hashlib.sha256(raw.encode('ascii')).hexdigest()


def _create_invite_locked(actor, band):
    # token_urlsafe(32) is a 256-bit random capability. Only its fixed-size hash persists.
    raw = secrets.token_urlsafe(32)
    invite = BandInviteCode(band_id=band.id, token_hash=_token_hash(raw), created_by_user_id=actor.id)
    db.session.add(invite)
    db.session.flush()
    audit('invite_created', actor, band=band, target=invite)
    return invite, raw


def create_invite(actor, band_id):
    _begin_privileged_mutation()
    band, _ = require_band_role(actor, band_id, lock=True)
    invite, raw = _create_invite_locked(actor, band)
    db.session.commit()
    return invite, raw


def invite_dict(invite):
    return {'id': invite.id, 'band_id': invite.band_id, 'created_at': invite.created_at.isoformat(),
            'revoked_at': invite.revoked_at.isoformat() if invite.revoked_at else None}


def _locked_invite_by_token_hash(token_hash):
    """Return an invite under the same row lock used by revoke and rotate."""
    return BandInviteCode.query.filter_by(
        token_hash=token_hash,
    ).with_for_update().populate_existing()


def revoke_invite(actor, band_id, invite_id):
    _begin_privileged_mutation()
    band, _ = require_band_role(actor, band_id, lock=True)
    invite = BandInviteCode.query.filter_by(
        id=invite_id, band_id=band.id,
    ).with_for_update().populate_existing().one_or_none()
    if invite is None:
        raise NotFoundError('Invite code not found')
    if invite.revoked_at is None:
        invite.revoked_at = now()
        invite.revoked_by_user_id = actor.id
        audit('invite_revoked', actor, band=band, target=invite)
        db.session.commit()
    return invite


def rotate_invite(actor, band_id, invite_id):
    """Revoke and replace one capability in the same authorized transaction."""
    _begin_privileged_mutation()
    band, _ = require_band_role(actor, band_id, lock=True)
    invite = BandInviteCode.query.filter_by(
        id=invite_id, band_id=band.id,
    ).with_for_update().populate_existing().one_or_none()
    if invite is None:
        raise NotFoundError('Invite code not found')
    if invite.revoked_at is None:
        invite.revoked_at = now()
        invite.revoked_by_user_id = actor.id
        audit('invite_revoked', actor, band=band, target=invite)
    replacement, raw = _create_invite_locked(actor, band)
    db.session.commit()
    return replacement, raw


def join_invite(user, raw):
    if not isinstance(raw, str) or not raw or len(raw) > 200:
        raise ValidationError('Invite code is invalid.', 404)
    # Share the invite row's serialization point with revoke/rotate.  In
    # SQLite this takes the writer lock before lookup; PostgreSQL emits FOR
    # UPDATE.  Do not move the revoked check before this lock.
    _begin_privileged_mutation()
    invite = _locked_invite_by_token_hash(_token_hash(raw)).one_or_none()
    if invite is None or invite.revoked_at is not None or invite.band.deleted_at is not None:
        raise ValidationError('Invite code is invalid.', 404)
    item = BandMembership.query.filter_by(band_id=invite.band_id, user_id=user.id).one_or_none()
    if item is None:
        item = BandMembership(band_id=invite.band_id, user_id=user.id, role='member', status=ACTIVE,
                              approved_at=now(), acted_by_user_id=user.id)
        db.session.add(item)
        try:
            db.session.flush()
            audit('membership_joined_by_invite', user, band=invite.band, target=item, details={'invite_id': invite.id})
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            item = BandMembership.query.filter_by(band_id=invite.band_id, user_id=user.id).one()
    elif item.status in {'pending', 'rejected', 'left'}:
        # An invite is an admission capability, not a role capability.  A
        # departed/rejected former administrator must re-enter as a member.
        item.role = 'member'
        item.status = ACTIVE
        item.requested_at = now()
        item.left_at = None
        item.approved_at = now()
        item.acted_by_user_id = user.id
        audit('membership_rejoined_by_invite', user, band=invite.band, target=item, details={'invite_id': invite.id})
        db.session.commit()
    return item


def set_role(actor, membership_id, role):
    if role not in {'admin', 'member'}:
        raise ValidationError('role must be admin or member.')
    _begin_privileged_mutation()
    item = BandMembership.query.filter_by(id=membership_id).with_for_update().populate_existing().one_or_none()
    if item is None:
        raise NotFoundError('Membership not found')
    band, actor_membership = require_band_role(actor, item.band_id, OWNER_ROLES, lock=True)
    if item.status != ACTIVE:
        raise ValidationError('Only active memberships can receive roles.', 409)
    if item.role == 'owner':
        raise ValidationError('Transfer ownership before changing an owner role.', 409)
    item.role = role
    item.acted_by_user_id = actor.id
    audit('membership_role_changed', actor, band=band, target=item, details={'role': role})
    db.session.commit()
    return item


def transfer_ownership(actor, band_id, target_membership_id):
    # Lock the band and both membership rows where supported.  The partial
    # unique index remains the cross-process final authority.
    _begin_privileged_mutation()
    band = Band.query.filter_by(id=band_id, deleted_at=None).with_for_update().populate_existing().one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    owner = BandMembership.query.filter_by(band_id=band.id, user_id=actor.id, status=ACTIVE,
                                           role='owner').with_for_update().populate_existing().one_or_none()
    if owner is None:
        raise NotFoundError('Band not found')
    target = BandMembership.query.filter_by(
        id=target_membership_id, band_id=band.id, status=ACTIVE,
    ).with_for_update().populate_existing().one_or_none()
    if target is None:
        raise NotFoundError('Membership not found')
    if target.id == owner.id:
        return target
    try:
        # Demote first so the non-deferrable SQLite/Postgres partial unique
        # index is respected.  The transaction remains atomic to readers.
        owner.role = 'admin'
        db.session.flush()
        target.role = 'owner'
        owner.acted_by_user_id = actor.id
        target.acted_by_user_id = actor.id
        audit('ownership_transferred', actor, band=band, target=target, details={'from_membership_id': owner.id})
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise ValidationError('Ownership transfer conflicted; retry.', 409) from error
    return target


def leave_band(actor, band_id):
    _begin_privileged_mutation()
    band, item = active_membership(actor, band_id)
    if item is None:
        raise NotFoundError('Band not found')
    if item.role == 'owner':
        other_owner = BandMembership.query.filter(BandMembership.band_id == band.id,
                                                  BandMembership.status == ACTIVE,
                                                  BandMembership.role == 'owner',
                                                  BandMembership.id != item.id).first()
        if other_owner is None:
            raise ValidationError('The sole owner must transfer ownership before leaving.', 409)
    item.status = 'left'
    item.left_at = now()
    audit('membership_left', actor, band=band, target=item)
    db.session.commit()
    return item


def close_band(actor, band_id):
    _begin_privileged_mutation()
    band, _ = require_band_role(actor, band_id, OWNER_ROLES, lock=True)
    if band.deleted_at is None:
        band.deleted_at = now()
        band.purge_after = band.deleted_at + timedelta(days=30)
        audit('band_closed', actor, band=band, target=band, details={'purge_after': band.purge_after.isoformat()})
        db.session.commit()
    return band
