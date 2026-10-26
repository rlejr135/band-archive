"""Source-key selection contracts, independent of ORM records and URL signing."""

from types import SimpleNamespace

import pytest

from media_processing import select_media_source_keys


@pytest.mark.parametrize(
    ('key_prefix', 'fields', 'expected'),
    [
        pytest.param('media', {},
                     {'original': None, 'video_720': None, 'audio': None},
                     id='missing-optional-keys'),
        pytest.param('media', {'filename': 'clip.mp4'},
                     {'original': 'media/clip.mp4', 'video_720': None, 'audio': None},
                     id='legacy-original-prefixed'),
        pytest.param('media', {'object_key': 'bands/7/media/new.mp4', 'filename': 'old.mp4'},
                     {'original': 'bands/7/media/new.mp4', 'video_720': None, 'audio': None},
                     id='canonical-original-wins'),
        pytest.param('media', {'object_key': '', 'filename': 'old.mp4'},
                     {'original': 'media/old.mp4', 'video_720': None, 'audio': None},
                     id='empty-canonical-original-falls-back'),
        pytest.param('media', {'object_key': None, 'filename': ''},
                     {'original': None, 'video_720': None, 'audio': None},
                     id='empty-legacy-original-is-absent'),
        pytest.param('media', {'object_key': 'original.mp4', 'filename': 'old.mp4',
                               'original_pruned_at': 'historical-prune-marker',
                               'video_720_object_key': 'bands/7/media/720.mp4'},
                     {'original': None, 'video_720': 'bands/7/media/720.mp4', 'audio': None},
                     id='pruned-original-never-falls-back'),
        pytest.param('media', {'filename': 'clip.mp4', 'original_pruned_at': None},
                     {'original': 'media/clip.mp4', 'video_720': None, 'audio': None},
                     id='unset-prune-marker-keeps-original'),
        pytest.param('media', {'video_720_filename': 'media/transcoded/720/old.mp4'},
                     {'original': None, 'video_720': 'media/transcoded/720/old.mp4', 'audio': None},
                     id='legacy-720-key-not-double-prefixed'),
        pytest.param('media', {'video_720_object_key': 'bands/7/media/720.mp4',
                               'video_720_filename': 'media/transcoded/720/old.mp4'},
                     {'original': None, 'video_720': 'bands/7/media/720.mp4', 'audio': None},
                     id='canonical-720-wins'),
        pytest.param('media', {'video_720_object_key': '', 'video_720_filename': '720/old.mp4'},
                     {'original': None, 'video_720': '720/old.mp4', 'audio': None},
                     id='empty-canonical-720-falls-back-without-prefix'),
        pytest.param('personal_logs', {'filename': 'practice.mp4',
                                       'video_720_object_key': 'unexpected/720.mp4',
                                       'video_720_filename': 'unexpected/legacy.mp4'},
                     {'original': 'personal_logs/practice.mp4', 'video_720': None, 'audio': None},
                     id='personal-log-never-selects-720'),
        pytest.param('media', {'file_type': 'audio', 'filename': 'song.m4a',
                               'transcoding_status': 'completed',
                               'video_720_object_key': 'unexpected/720.mp4',
                               'audio_object_key': 'unexpected/extracted.m4a'},
                     {'original': 'media/song.m4a', 'video_720': None, 'audio': None},
                     id='audio-upload-is-original-not-extracted-audio'),
        pytest.param('media', {'transcoding_status': 'completed',
                               'audio_object_key': 'bands/7/media/audio.m4a',
                               'audio_filename': 'old_audio.m4a'},
                     {'original': None, 'video_720': None, 'audio': 'bands/7/media/audio.m4a'},
                     id='completed-video-canonical-audio-wins'),
        pytest.param('personal_logs', {'transcoding_status': 'completed',
                                       'audio_object_key': '', 'audio_filename': 'practice_audio.m4a'},
                     {'original': None, 'video_720': None, 'audio': 'personal_logs/practice_audio.m4a'},
                     id='personal-log-completed-audio-legacy-fallback'),
        pytest.param('media', {'transcoding_status': 'completed', 'audio_filename': ''},
                     {'original': None, 'video_720': None, 'audio': None},
                     id='completed-without-audio-key-is-absent'),
        *[
            pytest.param('media', {'transcoding_status': status,
                                   'audio_object_key': 'bands/7/media/audio.m4a',
                                   'audio_filename': 'old_audio.m4a'},
                         {'original': None, 'video_720': None, 'audio': None},
                         id=f'noncompleted-video-hides-audio-{status}')
            for status in ('queued', 'processing', 'failed', None)
        ],
    ],
)
def test_source_key_contract_matrix(key_prefix, fields, expected):
    record = SimpleNamespace(file_type='video', transcoding_status='not_required')
    vars(record).update(fields)
    before = vars(record).copy()

    assert select_media_source_keys(record, key_prefix) == expected
    assert vars(record) == before
