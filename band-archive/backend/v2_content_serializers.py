"""Safe, tenant-aware serializers for the initial v2 content read API.

These serializers intentionally do not call model ``to_dict`` methods.  The
legacy serializers may create presigned URLs and retain storage-era filename
fields; neither is safe to reuse before a resource has passed v2 visibility
and tenant checks.  A direct, expiring R2 presigned URL may necessarily encode
its object path as part of the signed protocol; no separate storage-key or
filename field is returned.  ``display_name`` is the sole user-facing media
label and is derived only from the user-supplied ``original_filename`` after
basename/control-character sanitization.  Internal filenames and object keys
must never be used as display metadata.
"""

from band_access import MEMBERS, content_capabilities
from content_visibility import (
    belongs_to_scope, graph_is_scoped, media_visibility, rehearsal_visibility,
    song_visibility, visible_to_scope,
)
from media_sources import select_media_source_keys
from storage import storage


PUBLIC_URL_TTL = 3600
MEMBERS_URL_TTL = 300


def _iso(value):
    return value.isoformat() if value is not None else None


def _media_display_name(media):
    """Return a safe, user-facing label without consulting storage identifiers."""
    original_name = getattr(media, 'original_filename', None)
    if isinstance(original_name, str):
        # Treat either separator as a path boundary on every host OS.
        basename = original_name.replace('\\', '/').rsplit('/', 1)[-1]
        # Drop C0/C1 controls, including embedded newlines and terminal escapes.
        basename = ''.join(char for char in basename
                           if not (ord(char) < 32 or 0x7f <= ord(char) <= 0x9f)).strip()
        if basename:
            return basename[:200]
    file_type = getattr(media, 'file_type', None)
    kind = {'video': '영상', 'audio': '음원'}.get(file_type, '파일')
    return f'{kind} #{media.id}'


def _flags(scope, item, visibility):
    return {
        'visibility': getattr(item, 'visibility', visibility),
        'effective_visibility': visibility,
        **content_capabilities(scope, item),
    }


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


def rehearsal_ref(scope, rehearsal):
    from content_restrictions import require_unrestricted
    require_unrestricted(rehearsal)
    visibility = rehearsal_visibility(scope, rehearsal)
    return {
        'id': rehearsal.id, 'title': rehearsal.title, 'date': _iso(rehearsal.date),
        'rehearsal_type': rehearsal.rehearsal_type,
        **_flags(scope, rehearsal, visibility),
    }


def song_ref(scope, song):
    from content_restrictions import require_unrestricted
    require_unrestricted(song)
    visibility = song_visibility(scope, song)
    return {
        'id': song.id, 'title': song.title, 'artist': song.artist,
        **_flags(scope, song, visibility),
    }


def serialize_media(scope, media, viewer_vote=0):
    from content_restrictions import require_unrestricted
    require_unrestricted(media)
    if not graph_is_scoped(scope, media):
        raise ValueError('Media graph is outside the resolved band scope.')
    visibility = media_visibility(scope, media)
    return {
        'id': media.id,
        'song_id': media.song_id,
        'rehearsal_id': media.rehearsal_id,
        'file_type': media.file_type,
        'display_name': _media_display_name(media),
        'file_size': media.file_size,
        'is_featured': bool(media.is_featured),
        'upvote_count': media.upvote_count,
        'downvote_count': media.downvote_count,
        'vote_score': media.vote_score,
        # Account-scoped read routes supply this from a single page query.
        # It is never inferred from legacy device votes.
        'viewer_vote': viewer_vote if viewer_vote in (-1, 0, 1) else 0,
        'created_at': _iso(media.created_at),
        'playback': media_playback(media, visibility),
        **_flags(scope, media, visibility),
    }


def serialize_song(scope, song, *, include_children=True):
    from content_restrictions import is_restricted, require_unrestricted
    require_unrestricted(song)
    if not belongs_to_scope(scope, song):
        raise ValueError('Song is outside the resolved band scope.')
    visibility = song_visibility(scope, song)
    value = {
        'id': song.id, 'title': song.title, 'artist': song.artist, 'status': song.status,
        'chords': song.chords, 'link': song.link, 'memo': song.memo, 'genre': song.genre,
        'difficulty': song.difficulty, 'upvote_count': song.upvote_count,
        'downvote_count': song.downvote_count, 'vote_score': song.vote_score,
        'created_at': _iso(song.created_at), 'updated_at': _iso(song.updated_at),
        **_flags(scope, song, visibility),
    }
    if include_children:
        rehearsals = [rehearsal_ref(scope, item) for item in song.rehearsals
                      if belongs_to_scope(scope, item)
                      and not is_restricted(item)
                      and visible_to_scope(scope, rehearsal_visibility(scope, item))]
        value['rehearsals'] = sorted(rehearsals, key=lambda item: (item['date'] or '', item['id']))
    return value


def serialize_rehearsal(scope, rehearsal, *, include_children=True):
    from content_restrictions import is_restricted, require_unrestricted
    require_unrestricted(rehearsal)
    if not belongs_to_scope(scope, rehearsal):
        raise ValueError('Rehearsal is outside the resolved band scope.')
    visibility = rehearsal_visibility(scope, rehearsal)
    value = {
        'id': rehearsal.id, 'title': rehearsal.title, 'date': _iso(rehearsal.date),
        'start_date': _iso(rehearsal.start_date), 'end_date': _iso(rehearsal.end_date),
        'time': rehearsal.time, 'location': rehearsal.location, 'latitude': rehearsal.latitude,
        'longitude': rehearsal.longitude, 'memo': rehearsal.memo, 'color': rehearsal.color,
        'rehearsal_type': rehearsal.rehearsal_type,
        'created_at': _iso(rehearsal.created_at), 'updated_at': _iso(rehearsal.updated_at),
        **_flags(scope, rehearsal, visibility),
    }
    if include_children:
        value['songs'] = sorted(
            (song_ref(scope, song) for song in rehearsal.songs
             if belongs_to_scope(scope, song)
             and not is_restricted(song)
             and visible_to_scope(scope, song_visibility(scope, song))),
            key=lambda item: item['id'],
        )
    return value


def serialize_processing(scope, media):
    from content_restrictions import require_unrestricted
    require_unrestricted(media)
    visibility = media_visibility(scope, media)
    return {
        'id': media.id, 'file_type': media.file_type, 'status': media.transcoding_status,
        # Historic rows can retain exception messages and provider paths.
        # The read API exposes only a stable code, never that raw text.
        'error': 'processing_failed' if media.transcoding_status == 'failed' else None,
        'started_at': _iso(media.processing_started_at),
        'heartbeat_at': _iso(media.processing_heartbeat_at),
        'completed_at': _iso(media.processing_completed_at),
        **_flags(scope, media, visibility),
    }


def serialize_comment(scope, comment, media_visibility_value, children_by_parent=None, *, include_replies=True):
    from content_restrictions import is_restricted, require_unrestricted
    require_unrestricted(comment)
    value = {
        'id': comment.id, 'media_id': comment.media_id, 'parent_id': comment.parent_id,
        'author': comment.author, 'content': comment.content,
        'timestamp_seconds': comment.timestamp_seconds, 'created_at': _iso(comment.created_at),
        # Comments do not have an independent visibility column.  Their
        # declared/effective representation is explicitly inherited.
        **_flags(scope, comment, media_visibility_value),
    }
    if not include_replies:
        return value
    relation = (children_by_parent.get(comment.id, []) if children_by_parent is not None else comment.replies)
    replies = [reply for reply in relation if belongs_to_scope(scope, reply)
               and not is_restricted(reply)
               and reply.media_id == comment.media_id]
    value['replies'] = [serialize_comment(scope, reply, media_visibility_value, children_by_parent)
                        for reply in sorted(replies, key=lambda item: (_iso(item.created_at) or '', item.id))]
    return value
