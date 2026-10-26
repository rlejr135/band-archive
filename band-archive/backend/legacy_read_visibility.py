"""Optional-auth visibility checks for the public v1 compatibility reads.

Legacy write behavior remains separate. These helpers are used only by read
routes, after strict legacy tenant-graph validation and before any serializer
can mint a signed storage URL.
"""

from flask import after_this_request, g

from band_access import effective_visibility, read_scope_for_id
from errors import NotFoundError
from models import Band


def legacy_read_scope(band_id, *, roster=False):
    band_id = int(band_id)
    scopes = getattr(g, '_legacy_read_scopes', None)
    if scopes is None:
        scopes = {}
        g._legacy_read_scopes = scopes
    cached = scopes.get(band_id)
    if cached is None:
        scope = read_scope_for_id(band_id)
        band = Band.query.filter_by(id=scope.band_id, deleted_at=None).one_or_none()
        if band is None:
            raise NotFoundError('Resource not found')
        scopes[band_id] = (scope, band)
    else:
        scope, band = cached
    if roster and not scope.is_member and band.member_roster_public is not True:
        raise NotFoundError('Resource not found')

    if not getattr(g, '_legacy_private_cache_registered', False):
        @after_this_request
        def private_cache(response):
            response.headers['Cache-Control'] = 'private, no-store'
            response.vary.add('Authorization')
            return response
        g._legacy_private_cache_registered = True

    return scope


def legacy_item_visible(scope, *visibilities):
    if scope.is_member:
        return True
    band_visibility = getattr(scope, 'band_visibility', None)
    return effective_visibility(band_visibility, *visibilities) == 'PUBLIC'
