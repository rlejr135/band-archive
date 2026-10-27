"""Pre-extraction contracts for v2 object-level visibility rules."""

from types import SimpleNamespace

import pytest

from errors import ValidationError
from models import Media, Rehearsal, Song
from content_visibility import (
    announcement_visibility,
    belongs_to_scope,
    gallery_visibility,
    graph_is_scoped,
    media_visibility,
    personal_log_visibility,
    rehearsal_visibility,
    song_visibility,
    suggestion_visibility,
    visible_to_scope,
)


def _scope(*, band_id=11, band_visibility='PUBLIC', is_member=False):
    return SimpleNamespace(
        band_id=band_id,
        band_visibility=band_visibility,
        is_member=is_member,
    )


def _song(band_id=11, visibility='PUBLIC'):
    return Song(band_id=band_id, visibility=visibility, title='Song', artist='Artist')


def _rehearsal(band_id=11, visibility='PUBLIC'):
    return Rehearsal(
        band_id=band_id, visibility=visibility, title='Rehearsal',
        rehearsal_type='REGULAR',
    )


def _media(song, *, band_id=11, visibility='PUBLIC', rehearsal=None):
    item = Media(
        band_id=band_id, visibility=visibility, filename='media.mp4',
        song=song, rehearsal=rehearsal,
    )
    return item


@pytest.mark.parametrize(
    ('is_member', 'visibility', 'expected'),
    [(False, 'PUBLIC', True), (False, 'MEMBERS', False),
     (True, 'PUBLIC', True), (True, 'MEMBERS', True)],
)
def test_visible_to_scope_preserves_member_and_visitor_contract(is_member, visibility, expected):
    assert visible_to_scope(_scope(is_member=is_member), visibility) is expected


@pytest.mark.parametrize(
    ('band_id', 'has_band_id', 'expected'),
    [(11, True, True), (12, True, False), (None, True, False), (None, False, False)],
)
def test_belongs_to_scope_fails_closed_for_foreign_or_missing_band(band_id, has_band_id, expected):
    item = SimpleNamespace()
    if has_band_id:
        item.band_id = band_id
    assert belongs_to_scope(_scope(), item) is expected


@pytest.mark.parametrize(
    ('case', 'expected'),
    [('same_band_graph', True), ('foreign_media', False), ('foreign_song', False),
     ('foreign_direct_rehearsal', False), ('foreign_linked_rehearsal', False)],
)
def test_media_graph_scope_checks_each_loaded_parent(case, expected):
    song = _song()
    linked_rehearsal = _rehearsal()
    direct_rehearsal = _rehearsal()
    song.rehearsals = [linked_rehearsal]
    media = _media(song, rehearsal=direct_rehearsal)
    if case == 'foreign_media':
        media.band_id = 12
    elif case == 'foreign_song':
        song.band_id = 12
    elif case == 'foreign_direct_rehearsal':
        media.rehearsal.band_id = 12
    elif case == 'foreign_linked_rehearsal':
        linked_rehearsal.band_id = 12
    assert graph_is_scoped(_scope(), media) is expected


def test_song_and_rehearsal_graph_scope_validate_their_children():
    song = _song()
    foreign_media = _media(song, band_id=12)
    song.media_files = [foreign_media]
    assert graph_is_scoped(_scope(), song) is False

    rehearsal = _rehearsal()
    rehearsal.songs = [_song(band_id=12)]
    assert graph_is_scoped(_scope(), rehearsal) is False


def test_song_and_rehearsal_visibility_combine_band_and_resource():
    for band_visibility, resource_visibility, expected in [
            ('PUBLIC', 'PUBLIC', 'PUBLIC'), ('PUBLIC', 'MEMBERS', 'MEMBERS'),
            ('MEMBERS', 'PUBLIC', 'MEMBERS'), (None, 'PUBLIC', 'PUBLIC')]:
        scope = _scope(band_visibility=band_visibility)
        assert song_visibility(scope, SimpleNamespace(visibility=resource_visibility)) == expected
        assert rehearsal_visibility(scope, SimpleNamespace(visibility=resource_visibility)) == expected


@pytest.mark.parametrize(
    ('band_visibility', 'media_visibility_value', 'song_visibility_value',
     'linked_visibility', 'direct_visibility', 'expected'),
    [('PUBLIC', 'PUBLIC', 'PUBLIC', 'PUBLIC', 'PUBLIC', 'PUBLIC'),
     ('PUBLIC', 'MEMBERS', 'PUBLIC', 'PUBLIC', 'PUBLIC', 'MEMBERS'),
     ('PUBLIC', 'PUBLIC', 'MEMBERS', 'PUBLIC', 'PUBLIC', 'MEMBERS'),
     # A linked rehearsal constrains every media item for its song, even when
     # this media row also names another direct rehearsal.
     ('PUBLIC', 'PUBLIC', 'PUBLIC', 'MEMBERS', 'PUBLIC', 'MEMBERS'),
     ('PUBLIC', 'PUBLIC', 'PUBLIC', 'PUBLIC', 'MEMBERS', 'MEMBERS'),
     ('MEMBERS', 'PUBLIC', 'PUBLIC', 'PUBLIC', 'PUBLIC', 'MEMBERS')],
)
def test_media_visibility_includes_all_parent_constraints(
        band_visibility, media_visibility_value, song_visibility_value,
        linked_visibility, direct_visibility, expected):
    linked = _rehearsal(visibility=linked_visibility)
    direct = _rehearsal(visibility=direct_visibility)
    song = _song(visibility=song_visibility_value)
    song.rehearsals = [linked]
    media = _media(song, visibility=media_visibility_value, rehearsal=direct)
    assert media_visibility(_scope(band_visibility=band_visibility), media) == expected


def test_remaining_resource_visibility_combines_band_policy():
    for visibility_function in (personal_log_visibility, gallery_visibility,
                                announcement_visibility, suggestion_visibility):
        item = SimpleNamespace(visibility='MEMBERS')
        assert visibility_function(_scope(band_visibility='PUBLIC'), item) == 'MEMBERS'
        assert visibility_function(_scope(band_visibility='MEMBERS'), item) == 'MEMBERS'


def test_invalid_visibility_keeps_validation_error_contract():
    scope = _scope()
    checks = [
        lambda: song_visibility(scope, SimpleNamespace(visibility='PRIVATE')),
        lambda: rehearsal_visibility(scope, SimpleNamespace(visibility='PRIVATE')),
        lambda: media_visibility(scope, _media(_song(), visibility='PRIVATE')),
        lambda: personal_log_visibility(scope, SimpleNamespace(visibility='PRIVATE')),
        lambda: gallery_visibility(scope, SimpleNamespace(visibility='PRIVATE')),
        lambda: announcement_visibility(scope, SimpleNamespace(visibility='PRIVATE')),
        lambda: suggestion_visibility(scope, SimpleNamespace(visibility='PRIVATE')),
    ]
    for check in checks:
        with pytest.raises(ValidationError) as error:
            check()
        assert (error.value.message, error.value.status_code) == ('Invalid visibility.', 400)


def test_foreign_media_visibility_keeps_value_error_contract():
    media = _media(_song(), band_id=12)
    with pytest.raises(ValueError, match='Media graph is outside the resolved band scope\\.'):
        media_visibility(_scope(), media)
