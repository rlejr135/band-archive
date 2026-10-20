"""Authorization and transactional operations for the Phase 3 band boundary."""

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from errors import NotFoundError, ValidationError
from extensions import db
from models import (
    AppUser, AuditEvent, Band, BandCreationRequest, BandInviteCode, BandMembership,
    DirectUploadSession, LegacyBandClaimToken, MultipartUploadSession,
    PersonalLogMultipartUploadSession, Member,
)
from sqlalchemy import and_, or_
from member_profile_service import ensure_member_profile
from join_name import required_join_name


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
    if audience == 'self' and membership.status == 'pending':
        result['requested_name'] = membership.requested_name
    if audience == 'management':
        result.update({
            'status': membership.status,
            'requested_at': membership.requested_at.isoformat() if membership.requested_at else None,
            'approved_at': membership.approved_at.isoformat() if membership.approved_at else None,
            'left_at': membership.left_at.isoformat() if membership.left_at else None,
        })
        if membership.status == 'pending':
            result['introduction'] = membership.join_introduction
            result['requested_name'] = membership.requested_name
    return result


def band_dict(band, viewer=None, include_roster=False, membership=_UNRESOLVED_MEMBERSHIP):
    if membership is _UNRESOLVED_MEMBERSHIP:
        # Legacy callers may omit the explicit Phase 6 scope snapshot. Even
        # then, an inactive lifecycle row is never a band audience.
        membership = BandMembership.query.filter_by(
            band_id=band.id, user_id=viewer.id, status=ACTIVE,
        ).one_or_none() if viewer is not None else None
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
            'can_edit_band_profile': bool(active_member and membership.role == 'owner'),
            'can_leave': bool(active_member and membership.role in {'admin', 'member'}),
            'can_transfer_ownership': bool(active_member and membership.role == 'owner'),
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
        ensure_member_profile(owner)
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


def join_request(user, band_id, introduction=None, name=None):
    name = required_join_name(name)
    _begin_privileged_mutation()
    band = Band.query.filter_by(id=band_id, deleted_at=None).with_for_update().populate_existing().one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    item = BandMembership.query.filter_by(band_id=band.id, user_id=user.id).with_for_update().populate_existing().one_or_none()
    if item is not None:
        if item.status in {ACTIVE, 'pending'}:
            if item.status == ACTIVE:
                ensure_member_profile(item)
            elif item.requested_name is None:
                item.requested_name = name
            db.session.commit()
            return item, False
        if item.status in {'left', 'rejected'}:
            item.role = 'member'
            item.status = 'pending'
            item.requested_at = now()
            item.approved_at = None
            item.acted_by_user_id = user.id
            item.join_introduction = introduction
            item.requested_name = name
            audit('membership_rejoin_requested', user, band=band, target=item)
            db.session.commit()
            return item, True
    item = BandMembership(band_id=band.id, user_id=user.id, join_introduction=introduction,
                          requested_name=name)
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
    candidate = BandMembership.query.filter_by(id=membership_id).one_or_none()
    if candidate is None:
        raise NotFoundError('Membership not found')
    # Match leave/upload/transfer ordering: band first, then actor and target
    # memberships, so approval cannot deadlock with a concurrent leave.
    band, _ = require_band_role(actor, candidate.band_id, lock=True)
    item = BandMembership.query.filter_by(id=membership_id, band_id=band.id).with_for_update().populate_existing().one_or_none()
    if item is None:
        raise NotFoundError('Membership not found')
    if item.status != 'pending':
        if item.status == ACTIVE:
            ensure_member_profile(item)
        db.session.commit()
        return item, False
    try:
        item.status = ACTIVE if approved else 'rejected'
        item.join_introduction = None
        item.approved_at = now() if approved else None
        if approved:
            item.role = 'member'
            item.left_at = None
        item.acted_by_user_id = actor.id
        if approved:
            ensure_member_profile(item)
        item.requested_name = None
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
    candidate = BandInviteCode.query.filter_by(token_hash=_token_hash(raw)).one_or_none()
    if candidate is None:
        raise ValidationError('Invite code is invalid.', 404)
    # Match approval/leave/delete lock ordering: band, invite, membership.
    band = Band.query.filter_by(id=candidate.band_id, deleted_at=None).with_for_update().populate_existing().one_or_none()
    if band is None:
        raise ValidationError('Invite code is invalid.', 404)
    invite = _locked_invite_by_token_hash(_token_hash(raw)).one_or_none()
    if invite is None or invite.revoked_at is not None or invite.band.deleted_at is not None:
        raise ValidationError('Invite code is invalid.', 404)
    item = BandMembership.query.filter_by(band_id=invite.band_id, user_id=user.id).with_for_update().populate_existing().one_or_none()
    if item is None:
        item = BandMembership(band_id=invite.band_id, user_id=user.id, role='member', status=ACTIVE,
                              approved_at=now(), acted_by_user_id=user.id)
        db.session.add(item)
        try:
            db.session.flush()
            ensure_member_profile(item)
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
        item.join_introduction = None
        ensure_member_profile(item)
        audit('membership_rejoined_by_invite', user, band=invite.band, target=item, details={'invite_id': invite.id})
        db.session.commit()
    else:
        ensure_member_profile(item)
        db.session.commit()
    return item


_LEGACY_CLAIM_PURPOSE = 'initial_owner'
_LEGACY_CLAIM_TTL = timedelta(hours=24)


def legacy_claim_dict(claim):
    """Serialize token lifecycle metadata without exposing its digest."""
    return {
        'id': claim.id,
        'band_id': claim.band_id,
        'purpose': claim.purpose,
        'created_at': claim.created_at.isoformat() if claim.created_at else None,
        'expires_at': claim.expires_at.isoformat() if claim.expires_at else None,
        'invalidated_at': claim.invalidated_at.isoformat() if claim.invalidated_at else None,
        'redeemed_at': claim.redeemed_at.isoformat() if claim.redeemed_at else None,
    }


def eligible_legacy_claim_bands():
    """Return only server-verified ownerless legacy bands for an admin picker."""
    active_owner_exists = db.session.query(BandMembership.id).filter(
        BandMembership.band_id == Band.id,
        BandMembership.role == 'owner',
        BandMembership.status == ACTIVE,
    ).exists()
    return Band.query.filter(
        Band.deleted_at.is_(None),
        Band.is_legacy_import.is_(True),
        Band.created_by_user_id.is_(None),
        ~active_owner_exists,
    ).order_by(Band.name, Band.id).all()


def eligible_legacy_claim_band_dict(band):
    return {'id': band.id, 'name': band.name, 'slug': band.slug, 'description': band.description}


def _locked_legacy_band(band_id):
    """Lock the legacy-band transition resource before inspecting its state."""
    band = Band.query.filter_by(id=band_id, deleted_at=None).with_for_update().populate_existing().one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    return band


def _require_ownerless_legacy_band(band):
    if not band.is_legacy_import:
        raise ValidationError('Only an ownerless legacy band can be claimed.', 409)
    owner = BandMembership.query.filter_by(
        band_id=band.id, role='owner', status=ACTIVE,
    ).with_for_update().populate_existing().one_or_none()
    if band.created_by_user_id is not None or owner is not None:
        raise ValidationError('This legacy band already has an owner.', 409)
    return band


def _locked_ownerless_legacy_band(band_id):
    """Lock and verify a legacy band is still eligible for first ownership."""
    return _require_ownerless_legacy_band(_locked_legacy_band(band_id))


def _locked_outstanding_legacy_claim_query(band_id):
    return LegacyBandClaimToken.query.filter_by(
        band_id=band_id, purpose=_LEGACY_CLAIM_PURPOSE,
        invalidated_at=None, redeemed_at=None,
    ).with_for_update().populate_existing()


def _locked_outstanding_legacy_claim(band_id):
    return _locked_outstanding_legacy_claim_query(band_id).one_or_none()


def _create_legacy_claim_locked(actor, band, timestamp):
    # token_urlsafe(32) carries 256 bits; only a fixed-size digest reaches the database.
    raw = secrets.token_urlsafe(32)
    claim = LegacyBandClaimToken(
        band_id=band.id,
        purpose=_LEGACY_CLAIM_PURPOSE,
        token_hash=_token_hash(raw),
        issued_by_user_id=actor.id,
        expires_at=timestamp + _LEGACY_CLAIM_TTL,
    )
    db.session.add(claim)
    db.session.flush()
    audit('legacy_band_claim_issued', actor, band=band, target=claim,
          details={'purpose': _LEGACY_CLAIM_PURPOSE})
    return claim, raw


def _require_active_platform_admin(actor):
    if actor.status != ACTIVE or actor.is_platform_admin is not True:
        raise ValidationError('Platform administrator permission is required.', 403)


def issue_legacy_claim(actor, band_id):
    """Rotate the single outstanding initial-owner capability for a legacy band."""
    _require_active_platform_admin(actor)
    _begin_privileged_mutation()
    band = _locked_ownerless_legacy_band(band_id)
    timestamp = now()
    previous = _locked_outstanding_legacy_claim(band.id)
    if previous is not None:
        previous.invalidated_at = timestamp
        previous.invalidated_by_user_id = actor.id
        audit('legacy_band_claim_invalidated', actor, band=band, target=previous,
              details={'reason': 'rotated'})
        # Release the partial-unique slot before inserting the replacement.
        db.session.flush()
    claim, raw = _create_legacy_claim_locked(actor, band, timestamp)
    db.session.commit()
    return claim, raw


def invalidate_legacy_claim(actor, band_id):
    """Invalidate the current initial-owner capability without issuing another."""
    _require_active_platform_admin(actor)
    _begin_privileged_mutation()
    band = _locked_ownerless_legacy_band(band_id)
    claim = _locked_outstanding_legacy_claim(band.id)
    if claim is None:
        return None
    claim.invalidated_at = now()
    claim.invalidated_by_user_id = actor.id
    audit('legacy_band_claim_invalidated', actor, band=band, target=claim,
          details={'reason': 'manual'})
    db.session.commit()
    return claim


def _legacy_claim_expired(claim, timestamp):
    expires_at = claim.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= timestamp


def redeem_legacy_claim(user, band_id, raw):
    """Consume a valid capability and atomically establish the first owner."""
    if not isinstance(raw, str) or not raw or len(raw) > 200:
        raise ValidationError('Legacy ownership claim is invalid.', 404)
    if user.status != ACTIVE or user.email_verified is not True:
        raise ValidationError('An active, verified account is required to claim this band.', 403)

    _begin_privileged_mutation()
    # Read the candidate only to obtain its band lock key.  The authoritative
    # row is reloaded FOR UPDATE after the band lock, matching issuer order.
    candidate = LegacyBandClaimToken.query.filter_by(token_hash=_token_hash(raw)).one_or_none()
    if candidate is None or candidate.band_id != band_id:
        raise ValidationError('Legacy ownership claim is invalid.', 404)
    band = _locked_legacy_band(band_id)
    claim = LegacyBandClaimToken.query.filter_by(
        id=candidate.id, band_id=band.id, token_hash=_token_hash(raw),
    ).with_for_update().populate_existing().one_or_none()
    timestamp = now()
    if (claim is None or claim.purpose != _LEGACY_CLAIM_PURPOSE
            or claim.invalidated_at is not None or claim.redeemed_at is not None
            or _legacy_claim_expired(claim, timestamp)):
        raise ValidationError('Legacy ownership claim is invalid.', 404)
    _require_ownerless_legacy_band(band)

    membership = BandMembership.query.filter_by(
        band_id=band.id, user_id=user.id,
    ).with_for_update().populate_existing().one_or_none()
    try:
        band.created_by_user_id = user.id
        claim.redeemed_at = timestamp
        claim.redeemed_by_user_id = user.id
        if membership is None:
            membership = BandMembership(
                band_id=band.id, user_id=user.id, role='owner', status=ACTIVE,
                approved_at=timestamp, acted_by_user_id=user.id,
            )
            db.session.add(membership)
        else:
            membership.role = 'owner'
            membership.status = ACTIVE
            membership.join_introduction = None
            membership.requested_at = timestamp
            membership.left_at = None
            membership.approved_at = timestamp
            membership.acted_by_user_id = user.id
        db.session.flush()
        ensure_member_profile(membership)
        audit('legacy_band_claim_redeemed', user, band=band, target=claim,
              details={'purpose': _LEGACY_CLAIM_PURPOSE})
        audit('legacy_band_owner_claimed', user, band=band, target=membership,
              details={'role': 'owner'})
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise ValidationError('This legacy band already has an owner.', 409) from error
    return membership


def set_role(actor, membership_id, role):
    if role not in {'admin', 'member'}:
        raise ValidationError('role must be admin or member.')
    _begin_privileged_mutation()
    candidate = BandMembership.query.filter_by(id=membership_id).one_or_none()
    if candidate is None:
        raise NotFoundError('Membership not found')
    # Use band -> actor membership -> target membership ordering, matching
    # leave/transfer/upload so concurrent role changes cannot deadlock them.
    band, actor_membership = require_band_role(actor, candidate.band_id, OWNER_ROLES, lock=True)
    item = BandMembership.query.filter_by(id=membership_id, band_id=band.id).with_for_update().populate_existing().one_or_none()
    if item is None:
        raise NotFoundError('Membership not found')
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
        raise ValidationError('ownership_target_ineligible', 409)
    target_user = AppUser.query.filter_by(id=target.user_id).with_for_update().populate_existing().one_or_none()
    if target_user is None or target_user.status != ACTIVE or target_user.email_verified is not True:
        raise ValidationError('ownership_target_ineligible', 409)
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


def _has_completable_upload(actor_membership, band_id):
    """Check only this membership's live, nonlegacy upload reservations.

    This is a read-only query: leave never invokes expiry/abort cleanup or R2.
    Expired initiated rows cannot complete because the upload endpoints first
    expire them; completing rows remain blocking even past their expiry until
    the in-flight/recovery path reaches a terminal state.
    """
    timestamp = now().replace(tzinfo=None)
    initiated_live = and_(
        MultipartUploadSession.status == 'initiated',
        MultipartUploadSession.expires_at > timestamp,
    )
    personal_initiated_live = and_(
        PersonalLogMultipartUploadSession.status == 'initiated',
        PersonalLogMultipartUploadSession.expires_at > timestamp,
    )
    direct_initiated_live = and_(
        DirectUploadSession.status == 'initiated',
        DirectUploadSession.expires_at > timestamp,
    )
    media_upload = MultipartUploadSession.query.filter(
        MultipartUploadSession.band_id == band_id,
        MultipartUploadSession.created_by_membership_id == actor_membership.id,
        MultipartUploadSession.is_legacy.is_(False),
        or_(MultipartUploadSession.status == 'completing', initiated_live),
    ).first()
    if media_upload is not None:
        return True
    personal_upload = PersonalLogMultipartUploadSession.query.filter(
        PersonalLogMultipartUploadSession.band_id == band_id,
        PersonalLogMultipartUploadSession.created_by_membership_id == actor_membership.id,
        PersonalLogMultipartUploadSession.is_legacy.is_(False),
        or_(PersonalLogMultipartUploadSession.status == 'completing', personal_initiated_live),
    ).first()
    if personal_upload is not None:
        return True
    return DirectUploadSession.query.filter(
        DirectUploadSession.band_id == band_id,
        DirectUploadSession.created_by_membership_id == actor_membership.id,
        or_(DirectUploadSession.status == 'completing', direct_initiated_live),
    ).first() is not None


def leave_band(actor, band_id):
    _begin_privileged_mutation()
    band = Band.query.filter_by(id=band_id, deleted_at=None).with_for_update().populate_existing().one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    item = BandMembership.query.filter_by(band_id=band.id, user_id=actor.id).with_for_update().populate_existing().one_or_none()
    if item is None:
        raise NotFoundError('Band not found')
    if item.status == 'left':
        db.session.commit()
        return item
    if item.status != ACTIVE:
        raise NotFoundError('Band not found')
    if item.role == 'owner':
        raise ValidationError('ownership_transfer_required', 409)
    if _has_completable_upload(item, band.id):
        raise ValidationError('upload_in_progress', 409)
    item.status = 'left'
    item.left_at = now()
    audit('membership_left', actor, band=band, target=item)
    db.session.commit()
    return item


def membership_removal_access(actor, band_id, *, lock=False):
    """Authorize only roster removal, never a general cross-band bypass.

    All mutations serialize on the band before rereading DB-owned authority.
    No JWT metadata or client-supplied role participates in this decision.
    """
    band_query = Band.query.filter_by(id=band_id, deleted_at=None)
    if lock:
        band_query = band_query.with_for_update()
    band = band_query.populate_existing().one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    actor_query = AppUser.query.filter_by(id=actor.id)
    if lock:
        actor_query = actor_query.with_for_update()
    actor = actor_query.populate_existing().one_or_none()
    if actor is None or actor.status != ACTIVE or actor.email_verified is not True:
        raise ValidationError('Active verified account is required.', 403)
    membership_query = BandMembership.query.filter_by(band_id=band.id, user_id=actor.id, status=ACTIVE)
    if lock:
        membership_query = membership_query.with_for_update()
    membership = membership_query.populate_existing().one_or_none()
    if actor.is_platform_admin is not True:
        if membership is None:
            raise NotFoundError('Band not found')
        if membership.role != 'owner':
            raise ValidationError('Band owner permission is required.', 403)
    return band, actor, membership


def can_remove_from_band(actor, actor_membership, target):
    authorized = bool(actor and actor.status == ACTIVE and actor.email_verified is True and (
        actor.is_platform_admin is True or (
            actor_membership and actor_membership.user_id == actor.id
            and actor_membership.band_id == target.band_id
            and actor_membership.status == ACTIVE and actor_membership.role == 'owner'
        )
    ))
    return bool(authorized and target.status == ACTIVE and target.role != 'owner'
                and target.user_id != actor.id)


def membership_profile_names(band_id):
    return {item.linked_user_id: item.name for item in Member.query.filter(
        Member.band_id == band_id, Member.linked_user_id.is_not(None),
    ).all()}


def remove_membership(actor, band_id, membership_id):
    """Revoke membership immediately; retain accounts, profiles, and content.

    Unlike voluntary leave, an uploader cannot prevent removal with a live
    reservation. Upload materialization already rechecks the owner under this
    same band lock, so a removal that commits first prevents completion.
    """
    _begin_privileged_mutation()
    band, actor, _ = membership_removal_access(actor, band_id, lock=True)
    item = BandMembership.query.filter_by(
        id=membership_id, band_id=band.id,
    ).with_for_update().populate_existing().one_or_none()
    if item is None:
        raise NotFoundError('Membership not found')
    if item.user_id == actor.id or item.role == 'owner':
        raise ValidationError('membership_removal_target_ineligible', 409)
    if item.status == 'left':
        db.session.commit()
        return item, False
    if item.status != ACTIVE:
        raise ValidationError('membership_removal_target_ineligible', 409)
    item.status = 'left'
    item.left_at = now()
    item.acted_by_user_id = actor.id
    audit('membership_removed', actor, band=band, target=item)
    db.session.commit()
    return item, True


def close_band(actor, band_id):
    _begin_privileged_mutation()
    band, _ = require_band_role(actor, band_id, OWNER_ROLES, lock=True)
    if band.deleted_at is None:
        band.deleted_at = now()
        band.purge_after = band.deleted_at + timedelta(days=30)
        audit('band_closed', actor, band=band, target=band, details={'purge_after': band.purge_after.isoformat()})
        db.session.commit()
    return band


def update_band_profile(actor, band_id, payload):
    """Update only the owner-editable plain-text band description."""
    # Serialize authorization with ownership changes. PostgreSQL locks the
    # band/membership rows; SQLite claims its writer lock before rereading them.
    _begin_privileged_mutation()
    try:
        band, membership = require_band_role(actor, band_id, OWNER_ROLES, lock=True)
        if not isinstance(payload, dict) or set(payload) != {'description'}:
            raise ValidationError('Only the description field is allowed.')
        description = payload['description']
        if not isinstance(description, str):
            raise ValidationError('description must be text.')
        description = description.strip()
        if len(description) > 2000:
            raise ValidationError('description must be at most 2000 characters.')

        band.description = description or None
        audit('band_profile_updated', actor, band=band, target=band,
              details={'fields': ['description']})
        db.session.commit()
        return band, membership
    except Exception:
        db.session.rollback()
        raise
