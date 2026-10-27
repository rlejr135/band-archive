"""Focused contracts for the pre-extraction playback and signing boundary.

These direct serializer/helper calls verify signing policy after a caller has
already resolved and authorized its resource. HTTP authorization and the
existing non-member management-read contract are exercised in the v2 route
tests, not redefined by these helper-level cases.
"""

from datetime import datetime, timezone

import pytest

from band_access import PUBLIC, RequestAudience, read_scope_for_band
from errors import NotFoundError
from extensions import db
from models import (Band, BandMembership, ContentRestriction, GalleryImage, Media,
                    Member, PersonalLog, R2DeletionTask, Song)
from storage import storage
from tests.test_band_access_phase6 import access_app  # noqa: F401
from media_access_service import (
    MEMBERS_URL_TTL, PUBLIC_URL_TTL, media_playback,
    personal_log_playback as _personal_playback,
)
from v2_remaining_read_serializers import serialize_gallery_image


@pytest.fixture
def playback_rows(access_app):
    with access_app.app_context():
        band = Band.query.filter_by(slug='band-b').one()
        owner = BandMembership.query.filter_by(band_id=band.id, role='owner').one()
        song = Song(band_id=band.id, created_by_membership_id=owner.id,
                    visibility='PUBLIC', title='Playback contract', artist='Band B')
        db.session.add(song)
        db.session.flush()
        member = Member(band_id=band.id, name='Playback member', instrument='Bass')
        db.session.add(member)
        db.session.flush()
        media = Media(
            band_id=band.id, created_by_membership_id=owner.id, song_id=song.id,
            visibility='PUBLIC', filename='legacy.mp4', object_key='bands/b/media/original.mp4',
            file_type='video', transcoding_status='completed',
            video_720_filename='legacy-720.mp4', video_720_object_key='bands/b/media/720.mp4',
            audio_filename='legacy-audio.m4a', audio_object_key='bands/b/media/audio.m4a',
        )
        log = PersonalLog(
            band_id=band.id, member_id=member.id, created_by_membership_id=owner.id,
            visibility='PUBLIC', title='Personal log', filename='legacy-log.mov',
            object_key='bands/b/logs/original.mov', file_type='video',
            transcoding_status='completed', audio_filename='legacy-log.m4a',
            audio_object_key='bands/b/logs/audio.m4a',
        )
        gallery = GalleryImage(
            band_id=band.id, created_by_membership_id=owner.id, visibility='PUBLIC',
            filename='legacy-gallery.jpg', object_key='bands/b/gallery/canonical.jpg',
        )
        db.session.add_all((media, log, gallery))
        db.session.commit()
        scope = read_scope_for_band(band, audience=RequestAudience(user_id=None, subject=None))
        yield {'band_id': band.id, 'media_id': media.id, 'log_id': log.id,
               'gallery_id': gallery.id, 'scope': scope}


def _row(model, row_id):
    return db.session.get(model, row_id)


def _signer(monkeypatch):
    calls = []

    def generate_url(key, expires_in=None):
        calls.append((key, expires_in))
        return f'signed:{key}:{expires_in}'

    monkeypatch.setattr(storage, 'generate_url', generate_url)
    return calls


def test_video_prefers_720p_and_signs_completed_audio(playback_rows, monkeypatch):
    calls = _signer(monkeypatch)
    with db.session.begin_nested():
        media = _row(Media, playback_rows['media_id'])
        result = media_playback(media, PUBLIC)
    assert result['url'] == 'signed:bands/b/media/720.mp4:3600'
    assert result['video_url'] == result['url']
    assert result['audio_url'] == 'signed:bands/b/media/audio.m4a:3600'
    assert calls == [('bands/b/media/720.mp4', 3600), ('bands/b/media/audio.m4a', 3600)]


@pytest.mark.parametrize(('visibility', 'ttl'), [
    ('MEMBERS', 300), ('PUBLIC', 3600), (None, 3600), ('', 3600), ('UNKNOWN', 3600),
])
def test_media_ttl_uses_explicit_effective_visibility(playback_rows, monkeypatch, visibility, ttl):
    calls = _signer(monkeypatch)
    with db.session.begin_nested():
        media = _row(Media, playback_rows['media_id'])
        media.file_type = 'audio'
        media.transcoding_status = 'not_required'
        result = media_playback(media, visibility)
    assert result['url'] == f'signed:bands/b/media/original.mp4:{ttl}'
    assert result['audio_url'] is None
    assert calls == [('bands/b/media/original.mp4', ttl)]


@pytest.mark.parametrize(
    ('file_type', 'source_fields', 'expected_url', 'expected_video', 'expected_audio', 'expected_calls'),
    [
        ('video', {'object_key': None, 'filename': '', 'video_720_object_key': None,
                   'video_720_filename': None, 'audio_object_key': None, 'audio_filename': None},
         None, None, None, []),
        ('video', {'video_720_object_key': None, 'video_720_filename': None,
                   'audio_object_key': None, 'audio_filename': None},
         'signed:bands/b/media/original.mp4:3600', 'signed:bands/b/media/original.mp4:3600', None,
         [('bands/b/media/original.mp4', 3600)]),
        ('audio', {'object_key': None, 'filename': ''}, None, None, None, []),
    ],
)
def test_missing_or_fallback_media_sources(file_type, source_fields, expected_url, expected_video,
                                           expected_audio, expected_calls, playback_rows, monkeypatch):
    calls = _signer(monkeypatch)
    with db.session.begin_nested():
        media = _row(Media, playback_rows['media_id'])
        media.file_type = file_type
        media.transcoding_status = 'not_required'
        for name, value in source_fields.items():
            setattr(media, name, value)
        result = media_playback(media, PUBLIC)
    assert result['url'] == expected_url
    assert result['video_url'] == expected_video
    assert result['audio_url'] == expected_audio
    assert calls == expected_calls


def test_audio_upload_never_signs_extracted_audio(playback_rows, monkeypatch):
    calls = _signer(monkeypatch)
    with db.session.begin_nested():
        media = _row(Media, playback_rows['media_id'])
        media.file_type = 'audio'
        result = media_playback(media, PUBLIC)
    assert result['url'] == 'signed:bands/b/media/original.mp4:3600'
    assert result['video_url'] is None
    assert result['audio_url'] is None
    assert calls == [('bands/b/media/original.mp4', 3600)]


def test_pruned_video_never_signs_original_even_without_720p(playback_rows, monkeypatch):
    calls = _signer(monkeypatch)
    with db.session.begin_nested():
        media = _row(Media, playback_rows['media_id'])
        media.original_pruned_at = datetime.now(timezone.utc)
        media.video_720_object_key = None
        media.video_720_filename = None
        media.audio_object_key = None
        media.audio_filename = None
        result = media_playback(media, PUBLIC)
    assert result == {'url': None, 'video_url': None, 'audio_url': None, 'original_pruned': True}
    assert calls == []


@pytest.mark.parametrize(('kind', 'resource_type', 'model', 'helper'), [
    ('media', 'media', Media, 'media'),
    ('log', 'personal_log', PersonalLog, 'log'),
    ('gallery', 'gallery', GalleryImage, 'gallery'),
])
def test_pending_deletion_returns_no_playback_url_and_never_signs(
        playback_rows, monkeypatch, kind, resource_type, model, helper):
    calls = _signer(monkeypatch)
    row_id = playback_rows[f'{kind}_id']
    with db.session.begin_nested():
        db.session.add(R2DeletionTask(
            band_id=playback_rows['band_id'], resource_type=resource_type,
            resource_id=row_id, object_keys=[], status='queued',
        ))
        row = _row(model, row_id)
        if helper == 'media':
            result = media_playback(row, PUBLIC)
        elif helper == 'log':
            result = _personal_playback(row, PUBLIC)
        else:
            result = serialize_gallery_image(playback_rows['scope'], row)
    assert calls == []
    if helper == 'gallery':
        assert result['url'] is None
    else:
        assert result == {'url': None, 'video_url': None, 'audio_url': None, 'original_pruned': False}


@pytest.mark.parametrize(('resource_type', 'model_key', 'helper'), [
    ('media', 'media', 'media'), ('personal_log', 'log', 'log'), ('gallery', 'gallery', 'gallery'),
])
def test_restricted_resources_fail_before_signing(playback_rows, monkeypatch, resource_type, model_key, helper):
    calls = _signer(monkeypatch)
    model = {'media': Media, 'log': PersonalLog, 'gallery': GalleryImage}[model_key]
    row_id = playback_rows[f'{model_key}_id']
    with db.session.begin_nested():
        db.session.add(ContentRestriction(
            band_id=playback_rows['band_id'], resource_type=resource_type,
            resource_id=row_id, case_ref=f'case-{resource_type}', reason_code='privacy',
        ))
        row = _row(model, row_id)
        with pytest.raises(NotFoundError):
            if helper == 'media':
                media_playback(row, PUBLIC)
            elif helper == 'log':
                _personal_playback(row, PUBLIC)
            else:
                serialize_gallery_image(playback_rows['scope'], row)
    assert calls == []


def test_personal_log_never_selects_video_720_source(playback_rows, monkeypatch):
    calls = _signer(monkeypatch)
    with db.session.begin_nested():
        log = _row(PersonalLog, playback_rows['log_id'])
        result = _personal_playback(log, PUBLIC)
    assert result['url'] == 'signed:bands/b/logs/original.mov:3600'
    assert result['video_url'] == result['url']
    assert result['audio_url'] == 'signed:bands/b/logs/audio.m4a:3600'
    assert all('720' not in key for key, _ in calls)


@pytest.mark.parametrize(('object_key', 'filename', 'expected'), [
    ('bands/b/gallery/canonical.jpg', 'legacy.jpg', 'bands/b/gallery/canonical.jpg'),
    (None, 'legacy.jpg', 'gallery/legacy.jpg'),
])
def test_gallery_signs_canonical_key_then_legacy_fallback(playback_rows, monkeypatch,
                                                          object_key, filename, expected):
    calls = _signer(monkeypatch)
    with db.session.begin_nested():
        image = _row(GalleryImage, playback_rows['gallery_id'])
        image.object_key = object_key
        image.filename = filename
        result = serialize_gallery_image(playback_rows['scope'], image)
    assert result['url'] == f'signed:{expected}:{PUBLIC_URL_TTL}'
    assert calls == [(expected, PUBLIC_URL_TTL)]


def test_gallery_members_effective_visibility_uses_short_ttl(playback_rows, monkeypatch):
    calls = _signer(monkeypatch)
    with db.session.begin_nested():
        image = _row(GalleryImage, playback_rows['gallery_id'])
        image.visibility = 'MEMBERS'
        result = serialize_gallery_image(playback_rows['scope'], image)
    assert result['url'] == f'signed:bands/b/gallery/canonical.jpg:{MEMBERS_URL_TTL}'
    assert calls == [('bands/b/gallery/canonical.jpg', MEMBERS_URL_TTL)]


def test_gallery_reads_key_before_pending_check_but_does_not_sign_pending_item(
        playback_rows, monkeypatch):
    calls = _signer(monkeypatch)
    order = []
    import deletion_service
    monkeypatch.setattr(deletion_service, 'is_pending_deletion',
                        lambda *args: order.append('pending') or True)

    class KeyTrackedGallery:
        id = playback_rows['gallery_id']
        band_id = playback_rows['band_id']
        visibility = 'PUBLIC'
        filename = 'legacy.jpg'
        is_featured = False
        file_size = 1
        created_at = None

        @property
        def object_key(self):
            order.append('key')
            return 'bands/b/gallery/canonical.jpg'

    result = serialize_gallery_image(playback_rows['scope'], KeyTrackedGallery())
    assert result['url'] is None
    assert order == ['key', 'pending']
    assert calls == []


def test_gallery_metadata_failure_occurs_before_signing(playback_rows, monkeypatch):
    calls = _signer(monkeypatch)

    class BrokenMetadataGallery:
        id = playback_rows['gallery_id']
        band_id = playback_rows['band_id']
        visibility = 'PUBLIC'
        filename = 'legacy.jpg'
        object_key = 'bands/b/gallery/canonical.jpg'
        created_at = None

        @property
        def file_size(self):
            raise RuntimeError('broken metadata')

    with pytest.raises(RuntimeError, match='broken metadata'):
        serialize_gallery_image(playback_rows['scope'], BrokenMetadataGallery())
    assert calls == []
