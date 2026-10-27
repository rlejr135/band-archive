"""Construction-only contracts for the explicit band policy snapshot."""

from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace

import pytest

import band_access
from band_access import BandReadScope, RequestAudience, effective_visibility, read_scope_for_band
from extensions import db
from content_visibility import (
    _band_visibility as content_band_visibility,
    _band_visibility as remaining_band_visibility,
    song_visibility,
)


def _audience():
    return RequestAudience(user_id=None, subject=None)


def test_band_scope_requires_policy_without_changing_first_four_arguments():
    assert [field.name for field in fields(BandReadScope)] == [
        'band_id', 'band_slug', 'audience', 'membership', 'band_visibility',
    ]
    with pytest.raises(TypeError, match='band_visibility'):
        BandReadScope(71, 'snapshot', _audience(), None)


@pytest.mark.parametrize('visibility', ['PUBLIC', 'MEMBERS', None])
def test_scope_construction_is_query_free_and_preserves_policy(monkeypatch, visibility):
    def unexpected(*args, **kwargs):
        raise AssertionError('scope construction must not query or commit')

    for operation in ('query', 'execute', 'get', 'commit', 'flush'):
        monkeypatch.setattr(db.session, operation, unexpected)
    scope = BandReadScope(71, 'snapshot', _audience(), None, visibility)
    assert scope.band_visibility == visibility
    assert content_band_visibility(scope) == visibility
    assert remaining_band_visibility(scope) == visibility
    assert song_visibility(scope, SimpleNamespace(visibility='PUBLIC')) == effective_visibility(visibility)
    with pytest.raises(FrozenInstanceError):
        scope.band_visibility = 'PUBLIC'


@pytest.mark.parametrize('visibility', ['PUBLIC', 'MEMBERS', None])
def test_read_factory_snapshots_resolved_band_policy(monkeypatch, visibility):
    band = SimpleNamespace(id=71, slug='snapshot', deleted_at=None, default_visibility=visibility)
    membership = SimpleNamespace(role='member') if visibility == 'MEMBERS' else None
    monkeypatch.setattr(band_access, '_membership_for', lambda resolved, audience: membership)
    scope = read_scope_for_band(band, _audience())
    assert scope.band_visibility == visibility
    assert scope.membership is membership
    band.default_visibility = 'MEMBERS' if visibility == 'PUBLIC' else 'PUBLIC'
    assert scope.band_visibility == visibility


@pytest.mark.parametrize('accessor', [content_band_visibility, remaining_band_visibility])
def test_serializer_missing_policy_fallback_is_unchanged(accessor):
    assert accessor(SimpleNamespace()) == 'PUBLIC'
    assert accessor(SimpleNamespace(band_visibility=None)) is None
