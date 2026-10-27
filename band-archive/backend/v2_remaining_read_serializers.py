"""Safe serializers for the non-song Phase 6 read resources.

These deliberately use a separate module from legacy ``to_dict`` methods:
the latter expose storage-era filenames and other mutable implementation data.
"""

from band_access import content_capabilities
from content_visibility import (
    announcement_visibility, personal_log_visibility,
    suggestion_visibility,
)
from media_access_service import (
    gallery_image_url, personal_log_playback, prepare_gallery_access, prepare_personal_log_access,
)
from v2_content_serializers import _iso
from content_restrictions import require_unrestricted


def _flags(scope, item, visibility):
    return {
        'visibility': getattr(item, 'visibility', visibility),
        'effective_visibility': visibility,
        **content_capabilities(scope, item),
    }


def serialize_member(scope, member):
    require_unrestricted(member)
    from member_profile_service import profile_fields
    flags = content_capabilities(scope, member)
    own = bool(scope.is_member and scope.audience.account_active and scope.audience.email_verified
               and member.linked_user_id == scope.audience.user_id)
    # Roster queries contain active linked profiles, while detail reads may
    # retain a departed member's existing record links. Match the deletion
    # guard: only active linked profiles are protected from deletion.
    if (member.linked_user_id is not None
            and getattr(member, '_profile_membership_status', 'active') == 'active'):
        flags['can_delete'] = False
    flags['can_edit'] = own if member.linked_user_id is not None else flags['can_edit']
    return {
        'id': member.id,
        'name': member.name,
        'instrument': member.instrument,
        'created_at': _iso(member.created_at),
        **flags,
        **profile_fields(member, scope.audience.user_id, can_edit=own),
    }


def serialize_personal_log(scope, log):
    visibility = prepare_personal_log_access(scope, log)
    return {
        'id': log.id,
        'member_id': log.member_id,
        'member_name': log.member.name if log.member is not None else None,
        'title': log.title,
        'file_type': log.file_type,
        'file_size': log.file_size,
        'created_at': _iso(log.created_at),
        'playback': personal_log_playback(log, visibility),
        **_flags(scope, log, visibility),
    }


def serialize_personal_processing(scope, log):
    require_unrestricted(log)
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
    require_unrestricted(comment)
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
    prepared = prepare_gallery_access(scope, image)
    visibility = prepared.visibility
    return {
        'id': image.id,
        'file_size': image.file_size,
        'is_featured': bool(image.is_featured),
        'created_at': _iso(image.created_at),
        'url': gallery_image_url(prepared),
        **_flags(scope, image, visibility),
    }


def serialize_announcement(scope, announcement):
    require_unrestricted(announcement)
    visibility = announcement_visibility(scope, announcement)
    return {
        'id': announcement.id,
        'content': announcement.content,
        'updated_at': _iso(announcement.updated_at),
        **_flags(scope, announcement, visibility),
    }


def serialize_suggestion(scope, suggestion, viewer_vote=0):
    require_unrestricted(suggestion)
    visibility = suggestion_visibility(scope, suggestion)
    up = suggestion.thumbs_up or 0
    down = suggestion.thumbs_down or 0
    is_member = bool(scope.membership)
    is_author = is_member and suggestion.created_by_membership_id == scope.membership.id
    is_legacy_managed = bool(is_member and suggestion.created_by_membership_id is None and scope.can_manage)
    capabilities = content_capabilities(scope, suggestion)
    return {
        'id': suggestion.id,
        'title': suggestion.title,
        'artist': suggestion.artist,
        'link': suggestion.link,
        'memo': suggestion.memo,
        'thumbs_up': up,
        'thumbs_down': down,
        'vote_score': up - down,
        'viewer_vote': viewer_vote if viewer_vote in (-1, 0, 1) else 0,
        # Suggestions have no moderation-state column yet; the only safe
        # read representation is the current queue state.
        'status': 'pending',
        'created_at': _iso(suggestion.created_at),
        # Current member recommendations remain author-owned.  Managers can
        # steward only historical NULL-author rows, while ``can_manage``
        # separately exposes the admin/owner proceed action.
        'visibility': getattr(suggestion, 'visibility', visibility),
        'effective_visibility': visibility,
        'can_edit': bool(is_author or is_legacy_managed),
        'can_delete': capabilities['can_delete'],
        'can_manage': bool(scope.can_manage),
        'can_proceed': bool(scope.can_manage and capabilities['can_delete']),
    }
