"""Safe, tenant-aware serializers for the initial v2 content read API.

These serializers intentionally do not call model ``to_dict`` methods.  The
legacy serializers may create presigned URLs and retain storage-era filename
fields; neither is safe to reuse before a resource has passed v2 visibility
and tenant checks.
"""

from band_access import MEMBERS, PUBLIC, content_capabilities, effective_visibility
from media_processing import select_media_source_keys
from storage import storage


PUBLIC_URL_TTL = 3600
MEMBERS_URL_TTL = 300


def _iso(value):
    return value.isoformat() if value is not None else None


def _flags(scope, item, visibility):
    return {
        'visibility': getattr(item, 'visibility', visibility),
        'effective_visibility': visibility,
        **content_capabilities(scope, item),
    }


def visible_to_scope(scope, visibility):
    return scope.is_member or visibility == PUBLIC


def song_visibility(scope, song):
    return effective_visibility(scope and _band_visibility(scope), song.visibility)


def rehearsal_visibility(scope, rehearsal):
    return effective_visibility(_band_visibility(scope), rehearsal.visibility)


def media_visibility(scope, media):
    """Include every rehearsal relation that can constrain a media item."""
    values = [_band_visibility(scope), media.visibility]
    song = media.song
    if song is not None:
        values.append(song.visibility)
        # A song can be scheduled in more than one rehearsal.  A member-only
        # linked rehearsal is a parent constraint, even when this particular
        # media row uses a different direct rehearsal FK.
        values.extend(rehearsal.visibility for rehearsal in song.rehearsals)
    if media.rehearsal is not None:
        values.append(media.rehearsal.visibility)
    return effective_visibility(*values)


def _band_visibility(scope):
    # BandReadScope represents a URL-resolved live band.  The route supplies
    # this single field to keep serializers pure and avoids exposing models.
    return getattr(scope, 'band_visibility', PUBLIC)


def _attach_band_visibility(scope, visibility):
    """Attach the URL-resolved band policy to this request-local scope."""
    if hasattr(scope, 'band_visibility'):
        return scope
    object.__setattr__(scope, 'band_visibility', visibility)
    return scope


def scope_with_band_visibility(scope, band):
    return _attach_band_visibility(scope, band.default_visibility)


def media_playback(media, visibility):
    """Sign only playable sources, using a TTL derived from effective access."""
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
    visibility = rehearsal_visibility(scope, rehearsal)
    return {
        'id': rehearsal.id, 'title': rehearsal.title, 'date': _iso(rehearsal.date),
        **_flags(scope, rehearsal, visibility),
    }


def song_ref(scope, song):
    visibility = song_visibility(scope, song)
    return {
        'id': song.id, 'title': song.title, 'artist': song.artist,
        **_flags(scope, song, visibility),
    }


def serialize_media(scope, media):
    visibility = media_visibility(scope, media)
    return {
        'id': media.id,
        'song_id': media.song_id,
        'rehearsal_id': media.rehearsal_id,
        'file_type': media.file_type,
        'file_size': media.file_size,
        'is_featured': bool(media.is_featured),
        'upvote_count': media.upvote_count,
        'downvote_count': media.downvote_count,
        'vote_score': media.vote_score,
        'created_at': _iso(media.created_at),
        'playback': media_playback(media, visibility),
        **_flags(scope, media, visibility),
    }


def serialize_song(scope, song, *, include_children=True):
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
                      if visible_to_scope(scope, rehearsal_visibility(scope, item))]
        media = [serialize_media(scope, item) for item in song.media_files
                 if visible_to_scope(scope, media_visibility(scope, item))]
        value['rehearsals'] = sorted(rehearsals, key=lambda item: (item['date'] or '', item['id']))
        value['media'] = sorted(media, key=lambda item: (-item['vote_score'], item['id']))
    return value


def serialize_rehearsal(scope, rehearsal, *, include_children=True):
    visibility = rehearsal_visibility(scope, rehearsal)
    value = {
        'id': rehearsal.id, 'title': rehearsal.title, 'date': _iso(rehearsal.date),
        'start_date': _iso(rehearsal.start_date), 'end_date': _iso(rehearsal.end_date),
        'time': rehearsal.time, 'location': rehearsal.location, 'latitude': rehearsal.latitude,
        'longitude': rehearsal.longitude, 'memo': rehearsal.memo, 'color': rehearsal.color,
        'created_at': _iso(rehearsal.created_at), 'updated_at': _iso(rehearsal.updated_at),
        **_flags(scope, rehearsal, visibility),
    }
    if include_children:
        value['songs'] = sorted(
            (song_ref(scope, song) for song in rehearsal.songs
             if visible_to_scope(scope, song_visibility(scope, song))),
            key=lambda item: item['id'],
        )
        value['media'] = sorted(
            (serialize_media(scope, media) for media in rehearsal.media_files
             if visible_to_scope(scope, media_visibility(scope, media))),
            key=lambda item: (-item['vote_score'], item['id']),
        )
    return value


def serialize_processing(scope, media):
    visibility = media_visibility(scope, media)
    return {
        'id': media.id, 'file_type': media.file_type, 'status': media.transcoding_status,
        'error': media.processing_error, 'attempts': media.processing_attempts,
        'started_at': _iso(media.processing_started_at),
        'heartbeat_at': _iso(media.processing_heartbeat_at),
        'completed_at': _iso(media.processing_completed_at),
        **_flags(scope, media, visibility),
    }


def serialize_comment(scope, comment, media_visibility_value):
    value = {
        'id': comment.id, 'media_id': comment.media_id, 'parent_id': comment.parent_id,
        'author': comment.author, 'content': comment.content,
        'timestamp_seconds': comment.timestamp_seconds, 'created_at': _iso(comment.created_at),
        # Comments do not have an independent visibility column.  Their
        # declared/effective representation is explicitly inherited.
        **_flags(scope, comment, media_visibility_value),
    }
    replies = [reply for reply in comment.replies if reply.band_id == scope.band_id
               and reply.media_id == comment.media_id]
    value['replies'] = [serialize_comment(scope, reply, media_visibility_value)
                        for reply in sorted(replies, key=lambda item: (_iso(item.created_at) or '', item.id))]
    return value
