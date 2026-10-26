"""Central optional-authenticated read scopes for Phase 6 band resources.

The functions in this module deliberately take a resolved ``BandReadScope``
rather than a caller-provided band id.  A route first resolves a band through
its URL, then every content query is forced to retain that band predicate.
This keeps future resource routes from accidentally turning a global object
id into cross-band access.
"""

from dataclasses import dataclass
from functools import wraps

from flask import after_this_request, request
from sqlalchemy import false

from auth_service import authenticate_request
from band_service import ACTIVE, MANAGER_ROLES
from errors import NotFoundError, ValidationError
from models import Band, BandMembership


PUBLIC = 'PUBLIC'
MEMBERS = 'MEMBERS'
_VISIBILITY_ORDER = {PUBLIC: 0, MEMBERS: 1}


@dataclass(frozen=True)
class RequestAudience:
    """Immutable identity snapshot for an optional-authenticated request."""

    user_id: int | None
    subject: str | None
    account_active: bool = False
    email_verified: bool = False
    is_platform_admin: bool = False

    @property
    def authenticated(self):
        return self.user_id is not None


@dataclass(frozen=True)
class MembershipAudience:
    """Immutable active membership snapshot attached to a band read scope."""

    id: int
    band_id: int
    role: str
    status: str
    user_subject: str
    user_display_name: str | None
    user_email: str


@dataclass(frozen=True)
class BandReadScope:
    """Tenant-bound identity and resolved band-policy snapshot for one request."""

    band_id: int
    band_slug: str
    audience: RequestAudience
    membership: MembershipAudience | None
    band_visibility: str | None

    @property
    def is_member(self):
        return self.membership is not None

    @property
    def can_manage(self):
        return bool(self.membership and self.membership.role in MANAGER_ROLES)


def _has_authorization_header():
    # An explicitly empty Authorization header is malformed authentication,
    # not the absence of an authentication attempt.
    return 'Authorization' in request.headers


def current_audience():
    """Return the optional request identity, validating every supplied token.

    Only a truly absent Authorization header produces a visitor audience.
    ``current_app_user(required=True)`` reuses the existing Supabase JWT
    verifier and AppUser synchronization path; it raises its normal 401/403
    errors for malformed, expired, or otherwise invalid credentials.
    """
    request_object = request._get_current_object()
    cached = getattr(request_object, '_band_access_audience', None)
    if cached is not None:
        return cached
    if not _has_authorization_header():
        audience = RequestAudience(user_id=None, subject=None)
    else:
        # This is intentionally one authentication per Flask request object,
        # rather than a g-only cache.  It prevents a manually nested or
        # sequential request context from inheriting a prior request's user.
        user = authenticate_request()
        audience = RequestAudience(
            user_id=user.id, subject=user.supabase_user_id,
            account_active=user.status == ACTIVE,
            email_verified=user.email_verified is True,
            is_platform_admin=user.is_platform_admin is True,
        )
    setattr(request_object, '_band_access_audience', audience)
    return audience


def _optional_auth_cache_headers():
    """Make representation variation and user-specific data uncacheable."""
    @after_this_request
    def add_headers(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Authorization')
        return response


def optional_auth(view):
    """Authenticate when credentials are present and mark the response private."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        # Register before verification so handled AuthError responses receive
        # the same cache policy as successful optional-auth responses.
        _optional_auth_cache_headers()
        current_audience()
        return view(*args, **kwargs)
    return wrapped


def effective_visibility(*visibilities):
    """Return the most restrictive visibility from composable resource layers.

    ``None`` is the legacy/default-public value.  Parent route code can pass
    a band, resource, and child visibility without duplicating precedence.
    """
    normalized = [PUBLIC if value is None else value for value in visibilities]
    if not normalized:
        return PUBLIC
    if any(value not in _VISIBILITY_ORDER for value in normalized):
        raise ValidationError('Invalid visibility.', 400)
    return max(normalized, key=_VISIBILITY_ORDER.__getitem__)


def _membership_for(band, audience):
    if not audience.authenticated:
        return None
    membership = BandMembership.query.filter_by(
        band_id=band.id, user_id=audience.user_id, status=ACTIVE,
    ).one_or_none()
    if membership is None:
        return None
    return MembershipAudience(
        id=membership.id,
        band_id=membership.band_id,
        role=membership.role,
        status=membership.status,
        user_subject=membership.user.supabase_user_id,
        user_display_name=membership.user.display_name,
        user_email=membership.user.email,
    )


def read_scope_for_band(band, audience=None):
    """Resolve whether an audience may discover a particular band."""
    audience = audience if audience is not None else current_audience()
    if band is None or band.deleted_at is not None:
        raise NotFoundError('Band not found')
    membership = _membership_for(band, audience)
    if not membership and effective_visibility(band.default_visibility) != PUBLIC:
        # Read access intentionally conceals private bands and nonmember state.
        raise NotFoundError('Band not found')
    return BandReadScope(
        band_id=band.id,
        band_slug=band.slug,
        audience=audience,
        membership=membership,
        band_visibility=band.default_visibility,
    )


def read_scope_for_slug(slug, audience=None):
    """Resolve a URL slug before constructing its tenant-bound read scope."""
    value = slug.strip().casefold() if isinstance(slug, str) else ''
    band = Band.query.filter_by(slug=value, deleted_at=None).one_or_none()
    return read_scope_for_band(band, audience)


def read_scope_for_id(band_id, audience=None):
    """Resolve an integer URL tenant without ever falling back to a global id."""
    band = Band.query.filter(Band.id == band_id, Band.deleted_at.is_(None)).one_or_none()
    return read_scope_for_band(band, audience)


def visible_query(query, entity, scope, *, parent_visibility=None):
    """Apply mandatory band and audience visibility predicates to a query."""
    if not isinstance(scope, BandReadScope):
        raise TypeError('A resolved BandReadScope is required.')
    if not hasattr(entity, 'band_id') or not hasattr(entity, 'visibility'):
        raise TypeError('A tenant-visible entity requires band_id and visibility columns.')

    # Always bind the object query to the server-resolved URL band.  This is
    # deliberately unconditional even when callers have already filtered it.
    from content_restrictions import restriction_predicate
    query = query.filter(entity.band_id == scope.band_id, restriction_predicate(entity, band_id=scope.band_id))
    if not scope.is_member:
        query = query.filter(entity.visibility == PUBLIC)
    if not scope.is_member and effective_visibility(parent_visibility) != PUBLIC:
        query = query.filter(false())
    return query


def visible_object_or_404(entity, object_id, scope, *, parent_visibility=None):
    """Find an object only within a visible tenant scope, otherwise conceal it."""
    item = visible_query(entity.query, entity, scope, parent_visibility=parent_visibility).filter(
        entity.id == object_id
    ).one_or_none()
    if item is None:
        raise NotFoundError('Resource not found')
    return item


def content_capabilities(scope, content):
    """Compute content controls from active membership, never request input."""
    if not isinstance(scope, BandReadScope):
        raise TypeError('A resolved BandReadScope is required.')
    content_band_id = getattr(content, 'band_id', scope.band_id)
    if content_band_id != scope.band_id or not scope.is_member:
        return {'can_edit': False, 'can_delete': False, 'can_manage': False}
    can_delete = bool(
        scope.audience.authenticated
        and scope.audience.account_active
        and scope.audience.email_verified
        and (
            scope.membership.role == 'owner'
            or scope.audience.is_platform_admin
        )
    )
    if scope.can_manage:
        return {'can_edit': True, 'can_delete': can_delete, 'can_manage': True}
    created_by = getattr(content, 'created_by_membership_id', None)
    is_author = created_by is not None and created_by == scope.membership.id
    return {'can_edit': is_author, 'can_delete': can_delete, 'can_manage': False}


def require_operation_role(scope, roles=MANAGER_ROLES):
    """Reserve 403 for an authenticated in-band member lacking an operation role."""
    if not isinstance(scope, BandReadScope):
        raise TypeError('A resolved BandReadScope is required.')
    if not scope.is_member:
        raise NotFoundError('Band not found')
    if scope.membership.role not in roles:
        raise ValidationError('Insufficient band role.', 403)
    return scope.membership
