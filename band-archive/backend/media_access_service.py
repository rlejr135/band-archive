"""Coordinate v2 playback URL issuance after caller authorization.

The caller must retain its existing query and authorization checks, including
ordinary read and narrowly authorized management paths. Prepared gallery data
is not an authorization token. ORM attribute reads may trigger lazy loads.
"""

from dataclasses import dataclass

from band_access import MEMBERS
from content_visibility import (
    gallery_visibility, graph_is_scoped, media_visibility, personal_log_visibility,
)
from media_sources import select_media_source_keys
from storage import storage
from content_restrictions import require_unrestricted


PUBLIC_URL_TTL = 3600
MEMBERS_URL_TTL = 300


def prepare_media_access(scope, media):
    """Return effective visibility after existing graph checks for authorized media."""
    from content_restrictions import require_unrestricted
    require_unrestricted(media)
    if not graph_is_scoped(scope, media):
        raise ValueError('Media graph is outside the resolved band scope.')
    return media_visibility(scope, media)


def prepare_personal_log_access(scope, log):
    """Return effective visibility for an authorized log without adding graph checks."""
    require_unrestricted(log)
    return personal_log_visibility(scope, log)


def media_playback(media, visibility):
    """Sign only playable sources, using a TTL derived from effective access."""
    from deletion_service import is_pending_deletion
    from content_restrictions import require_unrestricted
    require_unrestricted(media)
    if is_pending_deletion('media', media.id, media.band_id):
        return {'url': None, 'video_url': None, 'audio_url': None,
                'original_pruned': bool(media.original_pruned_at)}
    ttl = MEMBERS_URL_TTL if visibility == MEMBERS else PUBLIC_URL_TTL
    sources = select_media_source_keys(media, 'media')
    video_url = None
    file_url = None
    if media.file_type == 'video':
        # ``select_media_source_keys`` makes a pruned original None.  Never
        # even ask storage to sign it; 720p is preferred when present.
        playback_key = sources['video_720'] or sources['original']
        video_url = storage.generate_url(playback_key, expires_in=ttl) if playback_key else None
    elif sources['original']:
        file_url = storage.generate_url(sources['original'], expires_in=ttl)
    audio_url = storage.generate_url(sources['audio'], expires_in=ttl) if sources['audio'] else None
    return {
        'url': video_url or file_url,
        'video_url': video_url,
        'audio_url': audio_url,
        'original_pruned': bool(media.original_pruned_at),
    }



def _ttl(visibility):
    return MEMBERS_URL_TTL if visibility == MEMBERS else PUBLIC_URL_TTL


def personal_log_playback(log, visibility):
    require_unrestricted(log)
    from deletion_service import is_pending_deletion
    if is_pending_deletion('personal_log', log.id, log.band_id):
        return {'url': None, 'video_url': None, 'audio_url': None,
                'original_pruned': bool(getattr(log, 'original_pruned_at', None))}
    sources = select_media_source_keys(log, 'personal_logs')
    # Personal logs do not currently have a 720p field.  The shared source
    # selector still provides canonical-first, pruned-safe original/audio
    # behavior and never exposes the selected keys themselves.
    original = sources['original']
    audio = sources['audio']
    original_url = storage.generate_url(original, expires_in=_ttl(visibility)) if original else None
    return {
        'url': original_url,
        'video_url': original_url if log.file_type == 'video' else None,
        'audio_url': storage.generate_url(audio, expires_in=_ttl(visibility)) if audio else None,
        'original_pruned': bool(getattr(log, 'original_pruned_at', None)),
    }


@dataclass(frozen=True)
class _GalleryAccess:
    visibility: object
    key: object
    pending: object


def prepare_gallery_access(scope, image):
    """Prepare URL inputs for an image already authorized by the caller."""
    require_unrestricted(image)
    from deletion_service import is_pending_deletion
    visibility = gallery_visibility(scope, image)
    key = image.object_key or (f'gallery/{image.filename}' if image.filename else None)
    pending = is_pending_deletion('gallery', image.id, image.band_id)
    return _GalleryAccess(visibility, key, pending)


def gallery_image_url(prepared):
    """Sign prepared inputs at the serializer's established URL position."""
    return (storage.generate_url(prepared.key, expires_in=_ttl(prepared.visibility))
            if prepared.key and not prepared.pending else None)
