"""Characterize the access preparation order used by v2 playback serializers.

These tests deliberately call the serializers directly. They use in-memory ORM
objects and replace only external checks/signing, so the contracts need no app
context or database fixture.
"""

from types import SimpleNamespace

import pytest

import content_restrictions
import content_visibility
import deletion_service
import media_access_service
import v2_content_serializers
import v2_remaining_read_serializers
from band_access import PUBLIC
from errors import NotFoundError
from models import Media, Member, PersonalLog, Song


def _media(*, band_id=7, song_band_id=None):
    song = Song(band_id=band_id if song_band_id is None else song_band_id,
                title='Song', artist='Artist', visibility=PUBLIC)
    song.rehearsals = []
    return Media(
        id=11, band_id=band_id, song_id=3, song=song, rehearsal=None,
        file_type='video', visibility=PUBLIC, original_filename='clip.mp4',
        filename='stored.mp4', object_key='bands/7/media/original.mp4',
        video_720_filename=None, video_720_object_key=None,
        audio_filename=None, audio_object_key=None, original_pruned_at=None,
        file_size=10, is_featured=False, upvote_count=0, downvote_count=0,
        vote_score=0, created_at=None,
    )


def _scope():
    return SimpleNamespace(band_id=7, band_visibility=PUBLIC)


def _media_order(monkeypatch, events):
    real_graph = content_visibility.graph_is_scoped
    real_visibility = content_visibility.media_visibility
    real_effective_visibility = content_visibility.effective_visibility

    def require(_record):
        events.append('restriction')

    def graph(scope, item):
        events.append('graph')
        return real_graph(scope, item)

    def visibility(scope, item):
        events.append('visibility')
        return real_visibility(scope, item)

    def effective_visibility(*values):
        events.append('visibility_calculation')
        return real_effective_visibility(*values)

    def display_name(item):
        events.append('metadata')
        return item.original_filename

    monkeypatch.setattr(content_restrictions, 'require_unrestricted', require)
    monkeypatch.setattr(v2_content_serializers, 'graph_is_scoped', graph)
    monkeypatch.setattr(v2_content_serializers, 'media_visibility', visibility)
    monkeypatch.setattr(content_visibility, 'graph_is_scoped', graph)
    monkeypatch.setattr(content_visibility, 'effective_visibility', effective_visibility)
    monkeypatch.setattr(v2_content_serializers, '_media_display_name', display_name)
    monkeypatch.setattr(v2_content_serializers, '_flags', lambda *_: {})


def _playback_order(monkeypatch, events, *, pending=False, fail_restriction_at=None):
    restriction_calls = 0

    def require(_record):
        nonlocal restriction_calls
        restriction_calls += 1
        events.append('restriction')
        if restriction_calls == fail_restriction_at:
            raise NotFoundError()

    monkeypatch.setattr(content_restrictions, 'require_unrestricted', require)
    monkeypatch.setattr(deletion_service, 'is_pending_deletion',
                        lambda *_: events.append('pending') or pending)
    monkeypatch.setattr(media_access_service, 'select_media_source_keys',
                        lambda *_: events.append('sources') or {
                            'original': 'bands/7/media/original.mp4',
                            'video_720': None, 'audio': None,
                        })
    monkeypatch.setattr(media_access_service.storage, 'generate_url',
                        lambda key, expires_in=None:
                        events.append(('sign', key, expires_in)) or f'signed:{key}')


def test_media_serializer_preserves_restriction_graph_visibility_metadata_sign_order(monkeypatch):
    events = []
    _media_order(monkeypatch, events)
    _playback_order(monkeypatch, events)

    result = v2_content_serializers.serialize_media(_scope(), _media())

    assert result['playback']['url'] == 'signed:bands/7/media/original.mp4'
    assert events == [
        'restriction', 'graph', 'visibility', 'graph', 'visibility_calculation', 'metadata',
        'restriction', 'pending', 'sources',
        ('sign', 'bands/7/media/original.mp4', 3600),
    ]


def test_media_initial_restriction_failure_stops_before_scope_metadata_and_signing(monkeypatch):
    events = []
    _media_order(monkeypatch, events)

    def reject(_record):
        events.append('restriction')
        raise NotFoundError()

    monkeypatch.setattr(content_restrictions, 'require_unrestricted', reject)
    monkeypatch.setattr(media_access_service.storage, 'generate_url',
                        lambda *_args, **_kwargs: events.append('sign'))

    with pytest.raises(NotFoundError):
        v2_content_serializers.serialize_media(_scope(), _media())

    assert events == ['restriction']


def test_media_foreign_parent_keeps_scope_value_error_before_visibility_metadata_and_signing(monkeypatch):
    events = []
    _media_order(monkeypatch, events)
    _playback_order(monkeypatch, events)
    media = _media(song_band_id=8)

    with pytest.raises(ValueError, match='^Media graph is outside the resolved band scope\\.$'):
        v2_content_serializers.serialize_media(_scope(), media)

    assert events == ['restriction', 'graph']


def test_media_metadata_failure_occurs_after_visibility_but_before_playback(monkeypatch):
    events = []
    _media_order(monkeypatch, events)
    _playback_order(monkeypatch, events)

    def broken_metadata(_item):
        events.append('metadata')
        raise RuntimeError('metadata failure')

    monkeypatch.setattr(v2_content_serializers, '_media_display_name', broken_metadata)
    with pytest.raises(RuntimeError, match='metadata failure'):
        v2_content_serializers.serialize_media(_scope(), _media())

    assert events == [
        'restriction', 'graph', 'visibility', 'graph', 'visibility_calculation', 'metadata',
    ]


def test_media_playback_second_restriction_failure_has_no_pending_check_or_signer(monkeypatch):
    events = []
    _media_order(monkeypatch, events)
    _playback_order(monkeypatch, events, fail_restriction_at=2)

    with pytest.raises(NotFoundError):
        v2_content_serializers.serialize_media(_scope(), _media())

    assert events == [
        'restriction', 'graph', 'visibility', 'graph', 'visibility_calculation', 'metadata',
        'restriction',
    ]


def test_media_pending_deletion_is_checked_after_metadata_and_never_signs(monkeypatch):
    events = []
    _media_order(monkeypatch, events)
    _playback_order(monkeypatch, events, pending=True)

    result = v2_content_serializers.serialize_media(_scope(), _media())

    assert result['playback']['url'] is None
    assert events == [
        'restriction', 'graph', 'visibility', 'graph', 'visibility_calculation', 'metadata',
        'restriction', 'pending',
    ]


def _personal_log(*, member=None):
    return PersonalLog(
        id=21, band_id=7, member_id=4, member=member or Member(name='Member'),
        visibility=PUBLIC, title='Practice', file_type='audio', file_size=10,
        created_at=None, filename='legacy.m4a', object_key='bands/7/logs/original.m4a',
        audio_filename=None, audio_object_key=None,
    )


def _personal_order(monkeypatch, events):
    real_visibility = content_visibility.personal_log_visibility
    real_effective_visibility = content_visibility.effective_visibility

    def require(_record):
        events.append('restriction')

    def visibility(scope, item):
        events.append('visibility')
        return real_visibility(scope, item)

    def effective_visibility(*values):
        events.append('visibility_calculation')
        return real_effective_visibility(*values)

    def iso(_value):
        events.append('metadata')
        return None

    monkeypatch.setattr(v2_remaining_read_serializers, 'require_unrestricted', require)
    monkeypatch.setattr(v2_remaining_read_serializers, 'personal_log_visibility', visibility)
    monkeypatch.setattr(content_visibility, 'effective_visibility', effective_visibility)
    monkeypatch.setattr(v2_remaining_read_serializers, '_iso', iso)
    monkeypatch.setattr(v2_remaining_read_serializers, '_flags', lambda *_: {})


def _personal_playback_order(monkeypatch, events, *, pending=False, fail_restriction_at=None):
    restriction_calls = 0

    def require(_record):
        nonlocal restriction_calls
        restriction_calls += 1
        events.append('restriction')
        if restriction_calls == fail_restriction_at:
            raise NotFoundError()

    monkeypatch.setattr(v2_remaining_read_serializers, 'require_unrestricted', require)
    monkeypatch.setattr(media_access_service, 'require_unrestricted', require)
    monkeypatch.setattr(deletion_service, 'is_pending_deletion',
                        lambda *_: events.append('pending') or pending)
    monkeypatch.setattr(media_access_service, 'select_media_source_keys',
                        lambda *_: events.append('sources') or {
                            'original': 'bands/7/logs/original.m4a',
                            'video_720': None, 'audio': None,
                        })
    monkeypatch.setattr(media_access_service.storage, 'generate_url',
                        lambda key, expires_in=None:
                        events.append(('sign', key, expires_in)) or f'signed:{key}')


def test_personal_log_serializer_preserves_restriction_visibility_metadata_sign_order(monkeypatch):
    events = []
    _personal_order(monkeypatch, events)
    _personal_playback_order(monkeypatch, events)

    result = v2_remaining_read_serializers.serialize_personal_log(_scope(), _personal_log())

    assert result['playback']['url'] == 'signed:bands/7/logs/original.m4a'
    assert events == [
        'restriction', 'visibility', 'visibility_calculation', 'metadata',
        'restriction', 'pending', 'sources',
        ('sign', 'bands/7/logs/original.m4a', 3600),
    ]


def test_personal_log_initial_restriction_failure_stops_before_visibility_metadata_and_signing(monkeypatch):
    events = []
    _personal_order(monkeypatch, events)

    def reject(_record):
        events.append('restriction')
        raise NotFoundError()

    monkeypatch.setattr(v2_remaining_read_serializers, 'require_unrestricted', reject)
    monkeypatch.setattr(media_access_service.storage, 'generate_url',
                        lambda *_args, **_kwargs: events.append('sign'))

    with pytest.raises(NotFoundError):
        v2_remaining_read_serializers.serialize_personal_log(_scope(), _personal_log())

    assert events == ['restriction']


def test_personal_log_visibility_failure_occurs_before_metadata_and_signing(monkeypatch):
    events = []
    _personal_order(monkeypatch, events)

    def broken_visibility(_scope, _log):
        events.append('visibility')
        raise ValueError('visibility failure')

    monkeypatch.setattr(v2_remaining_read_serializers, 'personal_log_visibility', broken_visibility)
    monkeypatch.setattr(media_access_service.storage, 'generate_url',
                        lambda *_args, **_kwargs: events.append('sign'))

    with pytest.raises(ValueError, match='visibility failure'):
        v2_remaining_read_serializers.serialize_personal_log(_scope(), _personal_log())

    assert events == ['restriction', 'visibility']


def test_personal_log_metadata_failure_occurs_after_visibility_before_playback(monkeypatch):
    events = []
    _personal_order(monkeypatch, events)
    _personal_playback_order(monkeypatch, events)

    def broken_iso(_value):
        events.append('metadata')
        raise RuntimeError('metadata failure')

    monkeypatch.setattr(v2_remaining_read_serializers, '_iso', broken_iso)
    with pytest.raises(RuntimeError, match='metadata failure'):
        v2_remaining_read_serializers.serialize_personal_log(_scope(), _personal_log())

    assert events == ['restriction', 'visibility', 'visibility_calculation', 'metadata']


def test_personal_log_playback_second_restriction_failure_has_no_pending_check_or_signer(monkeypatch):
    events = []
    _personal_order(monkeypatch, events)
    _personal_playback_order(monkeypatch, events, fail_restriction_at=2)

    with pytest.raises(NotFoundError):
        v2_remaining_read_serializers.serialize_personal_log(_scope(), _personal_log())

    assert events == [
        'restriction', 'visibility', 'visibility_calculation', 'metadata', 'restriction',
    ]


def test_personal_log_pending_deletion_is_checked_after_metadata_and_never_signs(monkeypatch):
    events = []
    _personal_order(monkeypatch, events)
    _personal_playback_order(monkeypatch, events, pending=True)

    result = v2_remaining_read_serializers.serialize_personal_log(_scope(), _personal_log())

    assert result['playback']['url'] is None
    assert events == [
        'restriction', 'visibility', 'visibility_calculation', 'metadata',
        'restriction', 'pending',
    ]
