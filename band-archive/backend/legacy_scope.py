"""Fail-closed tenant boundary for the public v1 compatibility API.

The old API has no band in its URL.  Its only safe interpretation after the
tenant migration is therefore the single, validated import band.  Keep that
decision here so a future v1 endpoint cannot accidentally turn a global id
into cross-tenant access.
"""

from flask import current_app
from sqlalchemy.exc import MultipleResultsFound

from errors import NotFoundError
from extensions import db
from models import TenantScopeUnavailable, validated_legacy_band_id


class LegacyScopeUnavailable(RuntimeError):
    """The database cannot prove which tenant owns v1 compatibility data."""


def legacy_band_id():
    """Return the only validated legacy-import band id, or fail closed.

    ``validated_legacy_band_id`` deliberately shares the ORM insertion guard's
    predicate and migration marker.  Convert all malformed/ambiguous results
    into one stable internal exception instead of silently selecting a band.
    """
    try:
        band_id = validated_legacy_band_id(db.session.connection())
    except (TenantScopeUnavailable, MultipleResultsFound) as exc:
        raise LegacyScopeUnavailable('Validated legacy tenant is unavailable.') from exc
    if not isinstance(band_id, int) or isinstance(band_id, bool):
        raise LegacyScopeUnavailable('Validated legacy tenant is unavailable.')
    return band_id


def legacy_query(model):
    """Start a query pinned to the sole legacy import band."""
    if not hasattr(model, 'band_id'):
        raise TypeError(f'{model.__name__} is not tenant-scoped')
    return model.query.filter(model.band_id == legacy_band_id())


def legacy_get_or_404(model, record_id, message=None):
    """Resolve a global-looking v1 id only within the legacy tenant."""
    item = legacy_query(model).filter(model.id == record_id).one_or_none()
    if item is None:
        raise NotFoundError(message or f'{model.__name__} not found')
    return item


def require_legacy_related(parent, related, message='Related resource not found'):
    """Reject an absent or cross-band relation before any mutation/storage IO."""
    if related is None or getattr(parent, 'band_id', None) != getattr(related, 'band_id', None):
        raise NotFoundError(message)
    if getattr(parent, 'band_id', None) != legacy_band_id():
        raise NotFoundError(message)
    return related


def legacy_v1_request():
    """Whether the current request is part of the public v1 compatibility API."""
    from flask import request

    if request.path == '/health' or request.path.startswith('/api/v2/'):
        return False
    # Internal inventory is an operator endpoint, not a historical frontend
    # route.  It has its own narrow authentication contract.
    if request.path.startswith('/internal/'):
        return False
    return True


def legacy_writes_enabled():
    return bool(current_app.config.get('LEGACY_V1_WRITES_ENABLED', True))
