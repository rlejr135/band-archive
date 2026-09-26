"""Safe serializers for the non-song Phase 6 read resources.

These deliberately use a separate module from legacy ``to_dict`` methods:
the latter expose storage-era filenames and other mutable implementation data.
"""

from band_access import MEMBERS, PUBLIC, content_capabilities, effective_visibility
from media_processing import select_media_source_keys
from storage import storage
from v2_content_serializers import MEMBERS_URL_TTL, PUBLIC_URL_TTL, _iso


def _flags(scope, item, visibility):
    return {
        'visibility': getattr(item, 'visibility', visibility),
        'effective_visibility': visibility,
        **content_capabilities(scope, item),
    }


def _band_visibility(scope):
    return getattr(scope, 'band_visibility', PUBLIC)


def personal_log_visibility(scope, log):
    return effective_visibility(_band_visibility(scope), log.visibility)


def gallery_visibility(scope, image):
    return effective_visibility(_band_visibility(scope), image.visibility)


def announcement_visibility(scope, announcement):
    return effective_visibility(_band_visibility(scope), announcement.visibility)


def suggestion_visibility(scope, suggestion):
    return effective_visibility(_band_visibility(scope), suggestion.visibility)


def _ttl(visibility):
    return MEMBERS_URL_TTL if visibility == MEMBERS else PUBLIC_URL_TTL


def _personal_playback(log, visibility):
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


def serialize_member(scope, member):
    return {
        'id': member.id,
        'name': member.name,
        'instrument': member.instrument,
        'created_at': _iso(member.created_at),
        **content_capabilities(scope, member),
    }


def serialize_personal_log(scope, log):
    visibility = personal_log_visibility(scope, log)
    return {
        'id': log.id,
        'member_id': log.member_id,
        'member_name': log.member.name if log.member is not None else None,
        'title': log.title,
        'file_type': log.file_type,
        'file_size': log.file_size,
        'created_at': _iso(log.created_at),
        'playback': _personal_playback(log, visibility),
        **_flags(scope, log, visibility),
    }


def serialize_personal_processing(scope, log):
    visibility = personal_log_visibility(scope, log)
    return {
        'id': log.id,
        'file_type': log.file_type,
        'status': log.transcoding_status,
        # Stored provider errors may include paths, credentials, or traces.
        'error': 'processing_failed' if log.transcoding_status == 'failed' else None,
        'started_at': _iso(log.processing_started_at),
        'heartbeat_at': _iso(log.processing_heartbeat_at),
        'completed_at': _iso(log.processing_completed_at),
        **_flags(scope, log, visibility),
    }


def serialize_personal_comment(scope, comment, visibility):
    return {
        'id': comment.id,
        'personal_log_id': comment.personal_log_id,
        'parent_id': comment.parent_id,
        'author': comment.author,
        'content': comment.content,
        'timestamp_seconds': comment.timestamp_seconds,
        'created_at': _iso(comment.created_at),
        # Comments inherit the accessible parent log's policy.
        **_flags(scope, comment, visibility),
    }


def serialize_gallery_image(scope, image):
    visibility = gallery_visibility(scope, image)
    key = image.object_key or (f'gallery/{image.filename}' if image.filename else None)
    return {
        'id': image.id,
        'file_size': image.file_size,
        'is_featured': bool(image.is_featured),
        'created_at': _iso(image.created_at),
        'url': storage.generate_url(key, expires_in=_ttl(visibility)) if key else None,
        **_flags(scope, image, visibility),
    }


def serialize_announcement(scope, announcement):
    visibility = announcement_visibility(scope, announcement)
    return {
        'id': announcement.id,
        'content': announcement.content,
        'updated_at': _iso(announcement.updated_at),
        **_flags(scope, announcement, visibility),
    }


def serialize_suggestion(scope, suggestion):
    visibility = suggestion_visibility(scope, suggestion)
    up = suggestion.thumbs_up or 0
    down = suggestion.thumbs_down or 0
    return {
        'id': suggestion.id,
        'title': suggestion.title,
        'artist': suggestion.artist,
        'link': suggestion.link,
        'memo': suggestion.memo,
        'thumbs_up': up,
        'thumbs_down': down,
        'vote_score': up - down,
        # Suggestions have no moderation-state column yet; the only safe
        # read representation is the current queue state.
        'status': 'pending',
        'created_at': _iso(suggestion.created_at),
        **_flags(scope, suggestion, visibility),
    }
