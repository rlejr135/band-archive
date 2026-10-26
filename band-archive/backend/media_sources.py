"""Select playback source keys without explicit storage or database access.

Reading deferred or expired ORM attributes can still trigger a lazy load;
callers retain responsibility for loading inputs and authorizing playback.
"""


def _canonical_or_legacy_key(record, canonical_field, legacy_field, key_prefix=None):
    canonical = getattr(record, canonical_field, None)
    if canonical:
        return canonical
    legacy = getattr(record, legacy_field, None)
    if not legacy:
        return None
    return f'{key_prefix}/{legacy}' if key_prefix else legacy


def select_media_source_keys(record, key_prefix):
    """Select active object keys without storage access or URL signing.

    Canonical tenant-aware keys take precedence over legacy filename fields.
    Historical original metadata remains available for audit and deletion, but
    a pruned original can never become a playback/signing source again.
    """
    original_key = None
    if not getattr(record, 'original_pruned_at', None):
        original_key = _canonical_or_legacy_key(record, 'object_key', 'filename', key_prefix)

    video_720_key = None
    if key_prefix == 'media' and record.file_type == 'video':
        video_720_key = _canonical_or_legacy_key(
            record, 'video_720_object_key', 'video_720_filename',
        )

    audio_key = None
    if record.file_type == 'video' and record.transcoding_status == 'completed':
        audio_key = _canonical_or_legacy_key(record, 'audio_object_key', 'audio_filename', key_prefix)

    return {'original': original_key, 'video_720': video_720_key, 'audio': audio_key}
