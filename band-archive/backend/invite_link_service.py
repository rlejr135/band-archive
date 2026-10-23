"""Short-lived, reusable band invitations and email-bound auth return context."""
import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta, timezone

from band_service import ACTIVE, MANAGER_ROLES, _begin_privileged_mutation, audit, now, require_band_role
from errors import NotFoundError, ValidationError
from extensions import db
from models import AppUser, Band, BandInviteLink, BandMembership, InviteResumeContext
from member_profile_service import activate_member_profile, ensure_member_profile
from voc_validation import selected_member_position_values
from join_name import required_join_name

_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
_EMAIL_RE = re.compile(r'^[^\s@]{1,64}@[^\s@]{1,255}$')
_lock = threading.Lock()
_windows = defaultdict(deque)
_global_context_issuance = deque()


class RateLimited(ValidationError):
    def __init__(self, retry_after=60):
        super().__init__('Request limit reached; try again later.', 429)
        self.retry_after = retry_after


def _hit(key, limit, seconds):
    current = time.monotonic()
    with _lock:
        bucket = _windows.get(key)
        if bucket is None:
            if len(_windows) >= 20_000:
                for stale_key, stale_events in list(_windows.items()):
                    while stale_events and stale_events[0] <= current - 3600:
                        stale_events.popleft()
                    if not stale_events:
                        _windows.pop(stale_key, None)
                if len(_windows) >= 20_000:
                    raise RateLimited(60)
            bucket = deque()
            _windows[key] = bucket
        while bucket and bucket[0] <= current - seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise RateLimited(max(1, int(seconds - (current - bucket[0]))))
        bucket.append(current)


def rate_limit(bucket, limit, seconds, *, actor=None):
    remote = request_address_hash()
    if actor is None:
        _hit((bucket, 'ip', remote), limit, seconds)
    else:
        _hit((bucket, 'user', str(actor.id)), limit, seconds)
        _hit((bucket, 'ip', remote), limit, seconds)


def request_address_hash():
    from flask import request
    address = request.remote_addr or 'unknown'
    return hashlib.sha256(address.encode('utf-8', 'replace')).hexdigest()


def canonical_token(value):
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        return False
    try:
        decoded = base64.urlsafe_b64decode(value + '=')
    except (ValueError, TypeError):
        return False
    return len(decoded) == 32 and base64.urlsafe_b64encode(decoded).decode('ascii').rstrip('=') == value


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def utc_iso(value):
    return _utc(value).isoformat()


def _digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def _email(value):
    if not isinstance(value, str) or len(value) > 320:
        raise ValidationError('A valid email address is required.')
    normalized = value.strip().casefold()
    if not _EMAIL_RE.fullmatch(normalized):
        raise ValidationError('A valid email address is required.')
    return normalized


def _email_hmac(context, email):
    message = b'band-invite-email-v1\0' + email.encode('utf-8')
    return hmac.new(context.encode('ascii'), message, hashlib.sha256).hexdigest()


def _live_link_by_token(token):
    if not canonical_token(token):
        return None
    timestamp = now()
    return BandInviteLink.query.join(Band).filter(
        BandInviteLink.token_hash == _digest(token), BandInviteLink.revoked_at.is_(None),
        BandInviteLink.expires_at > timestamp, Band.deleted_at.is_(None),
    ).with_entities(BandInviteLink, Band).one_or_none()


def link_metadata(link):
    return {'id': link.id, 'band_id': link.band_id,
            'created_at': utc_iso(link.created_at), 'expires_at': utc_iso(link.expires_at),
            'revoked_at': utc_iso(link.revoked_at) if link.revoked_at else None}


def create_link(actor, band_id):
    _begin_privileged_mutation()
    band, _ = require_band_role(actor, band_id, MANAGER_ROLES, lock=True)
    timestamp = now()
    token = secrets.token_urlsafe(32)
    item = BandInviteLink(band_id=band.id, token_hash=_digest(token), created_by_user_id=actor.id,
                          created_at=timestamp, expires_at=timestamp + timedelta(hours=24))
    db.session.add(item)
    db.session.flush()
    audit('invite_link_created', actor, band=band, target=item)
    db.session.commit()
    return item, token


def list_links(actor, band_id, limit=20, offset=0):
    require_band_role(actor, band_id, MANAGER_ROLES)
    query = BandInviteLink.query.filter_by(band_id=band_id)
    total = query.count()
    rows = query.order_by(BandInviteLink.created_at.desc(), BandInviteLink.id.desc()).offset(offset).limit(limit).all()
    return rows, {'limit': limit, 'offset': offset, 'total': total, 'has_more': offset + len(rows) < total}


def revoke_link(actor, band_id, link_id):
    _begin_privileged_mutation()
    band = Band.query.filter_by(id=band_id).with_for_update().populate_existing().one_or_none()
    if band is None or band.deleted_at is not None:
        raise NotFoundError('Band not found')
    # Check authorization before revealing whether a link exists. The band
    # lock serializes membership role changes, then the link lock preserves
    # the shared band -> invite -> membership lock order used by invite flows.
    membership = BandMembership.query.filter_by(
        band_id=band.id, user_id=actor.id, status=ACTIVE,
    ).populate_existing().one_or_none()
    if membership is None:
        raise NotFoundError('Band not found')
    if membership.role not in MANAGER_ROLES:
        raise ValidationError('Insufficient band role.', 403)
    item = BandInviteLink.query.filter_by(id=link_id, band_id=band_id).with_for_update().one_or_none()
    if item is None:
        raise NotFoundError('Invitation not found')
    membership = BandMembership.query.filter_by(
        band_id=band.id, user_id=actor.id, status=ACTIVE,
    ).with_for_update().populate_existing().one_or_none()
    if membership is None:
        raise NotFoundError('Band not found')
    if membership.role not in MANAGER_ROLES:
        raise ValidationError('Insufficient band role.', 403)
    if item.revoked_at is None:
        item.revoked_at = now()
        item.revoked_by_user_id = actor.id
        audit('invite_link_revoked', actor, band=band, target=item)
    db.session.commit()
    return item


def _lock_band_for_link(token):
    if not canonical_token(token):
        raise NotFoundError('Invitation not found')
    link_row = BandInviteLink.query.filter_by(token_hash=_digest(token)).one_or_none()
    if link_row is None:
        raise NotFoundError('Invitation not found')
    band = Band.query.filter_by(id=link_row.band_id).with_for_update().populate_existing().one_or_none()
    if band is None or band.deleted_at is not None:
        raise NotFoundError('Invitation not found')
    link = BandInviteLink.query.filter_by(id=link_row.id).with_for_update().populate_existing().one_or_none()
    if (link is None or link.revoked_at is not None or _utc(link.expires_at) <= now()):
        raise NotFoundError('Invitation not found')
    return band, link


def create_resume_context(token, email, address):
    normalized = _email(email)
    _hit(('resume-context-ip', address), 10, 600)
    current = time.monotonic()
    with _lock:
        while _global_context_issuance and _global_context_issuance[0] <= current - 600:
            _global_context_issuance.popleft()
        if len(_global_context_issuance) >= 60:
            raise RateLimited(max(1, int(600 - (current - _global_context_issuance[0]))))
        _global_context_issuance.append(current)
    _begin_privileged_mutation()
    band, link = _lock_band_for_link(token)
    timestamp = now()
    # Expired contexts may be removed in a bounded same-band batch. Completed
    # contexts remain as audit-safe replay tombstones but do not consume caps.
    old_ids = db.session.query(InviteResumeContext.id).join(BandInviteLink).filter(
        BandInviteLink.band_id == band.id, InviteResumeContext.expires_at <= timestamp,
    ).order_by(InviteResumeContext.id).limit(100).all()
    if old_ids:
        db.session.query(InviteResumeContext).filter(
            InviteResumeContext.id.in_([item[0] for item in old_ids]),
        ).delete(synchronize_session=False)
    live = InviteResumeContext.expires_at > timestamp
    incomplete = InviteResumeContext.completed_at.is_(None)
    by_link = InviteResumeContext.query.filter_by(invite_link_id=link.id).filter(live, incomplete).count()
    by_band = InviteResumeContext.query.join(BandInviteLink).filter(
        BandInviteLink.band_id == band.id, live, incomplete,
    ).count()
    if by_link >= 200 or by_band >= 1000:
        db.session.rollback()
        raise RateLimited(60)
    context = secrets.token_urlsafe(32)
    expiry = min(timestamp + timedelta(hours=1), _utc(link.expires_at))
    row = InviteResumeContext(invite_link_id=link.id, context_hash=_digest(context),
                              expected_email_hmac=_email_hmac(context, normalized),
                              created_at=timestamp, expires_at=expiry)
    db.session.add(row)
    db.session.commit()
    return context, expiry


def _resolve_context(context, actor, *, allow_claim=True):
    if not canonical_token(context):
        raise NotFoundError('Invitation not found')
    row = InviteResumeContext.query.filter_by(context_hash=_digest(context)).one_or_none()
    if row is None:
        raise NotFoundError('Invitation not found')
    linkrow = db.session.get(BandInviteLink, row.invite_link_id)
    band = Band.query.filter_by(id=linkrow.band_id if linkrow else None).with_for_update().populate_existing().one_or_none()
    if band is None or band.deleted_at is not None:
        raise NotFoundError('Invitation not found')
    link = BandInviteLink.query.filter_by(id=row.invite_link_id).with_for_update().populate_existing().one_or_none()
    row = InviteResumeContext.query.filter_by(id=row.id).with_for_update().populate_existing().one_or_none()
    timestamp = now()
    if (link is None or row is None or link.revoked_at is not None or _utc(link.expires_at) <= timestamp
            or _utc(row.expires_at) <= timestamp):
        raise NotFoundError('Invitation not found')
    actor = AppUser.query.filter_by(id=actor.id).with_for_update().populate_existing().one_or_none()
    if actor is None or actor.status != ACTIVE or actor.email_verified is not True:
        raise ValidationError('A verified active account is required.', 403)
    expected = _email_hmac(context, actor.email.strip().casefold())
    if not hmac.compare_digest(expected, row.expected_email_hmac):
        raise NotFoundError('Invitation not found')
    if row.claimed_user_id is not None and row.claimed_user_id != actor.id:
        raise NotFoundError('Invitation not found')
    if row.claimed_user_id is None:
        if not allow_claim:
            raise NotFoundError('Invitation not found')
        row.claimed_user_id = actor.id
        row.claimed_at = timestamp
    return band, link, row, actor


def resume(context, actor):
    rate_limit('invite-resume', 20, 60, actor=actor)
    _begin_privileged_mutation()
    band, link, row, actor = _resolve_context(context, actor)
    db.session.commit()
    from invite_introduction import introduction_payload
    payload = introduction_payload(band, actor)
    payload.pop('viewer', None)
    payload.update({'id': link.id, 'expires_at': _utc(link.expires_at).isoformat()})
    return {'context': context, 'expires_at': _utc(row.expires_at).isoformat(), 'invite': payload,
            'viewer': _viewer(actor, band.id)}


def _viewer(actor, band_id):
    item = BandMembership.query.filter_by(band_id=band_id, user_id=actor.id).one_or_none()
    return {'membership_status': item.status if item else None,
            'can_open_workspace': bool(item and item.status == ACTIVE)}


def accept(context, actor, name=None, positions=None, custom_position=None):
    rate_limit('invite-accept', 20, 60, actor=actor)
    _begin_privileged_mutation()
    band, link, row, actor = _resolve_context(context, actor, allow_claim=False)
    existing = BandMembership.query.filter_by(band_id=band.id, user_id=actor.id).with_for_update().one_or_none()
    if row.completed_at is not None:
        if existing is None or existing.status != ACTIVE:
            raise NotFoundError('Invitation not found')
        db.session.commit()
        return {'joined': False, 'already_member': True, 'band': {'id': band.id, 'name': band.name, 'slug': band.slug},
                'membership': {'status': existing.status, 'role': existing.role}}
    if existing is not None and existing.status == ACTIVE:
        row.completed_at = now()
        ensure_member_profile(existing)
        db.session.commit()
        return {'joined': False, 'already_member': True, 'band': {'id': band.id, 'name': band.name, 'slug': band.slug},
                'membership': {'status': existing.status, 'role': existing.role}}
    name = required_join_name(name)
    position_selection_supplied = positions is not None or custom_position is not None
    selected_positions = (selected_member_position_values(
        positions, '' if custom_position is None else custom_position,
    ) if position_selection_supplied else None)
    if existing is None:
        existing = BandMembership(band_id=band.id, user_id=actor.id, role='member', status=ACTIVE,
                                  requested_at=now(), approved_at=now(), left_at=None,
                                  acted_by_user_id=None, join_introduction=None)
        db.session.add(existing)
        db.session.flush()
    elif existing.status != ACTIVE:
        existing.role = 'member'
        existing.status = ACTIVE
        existing.requested_at = now()
        existing.approved_at = now()
        existing.left_at = None
        existing.acted_by_user_id = None
        existing.join_introduction = None
    row.completed_at = now()
    existing.requested_name = name
    if selected_positions:
        activate_member_profile(existing, name, positions=selected_positions['positions'],
                                custom_position=selected_positions['custom_position'])
    else:
        activate_member_profile(existing, name)
    existing.requested_name = None
    existing.requested_positions_json = None
    existing.requested_custom_position = None
    audit('membership_joined_by_invite_link', actor, band=band, target=existing)
    db.session.commit()
    return {'joined': True, 'already_member': False, 'band': {'id': band.id, 'name': band.name, 'slug': band.slug},
            'membership': {'status': existing.status, 'role': existing.role}}
