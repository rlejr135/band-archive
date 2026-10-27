"""Visibility and tenant-graph rules for band content reads.

The functions do not issue direct database queries, commit transactions, or
sign storage URLs. Reading ORM attributes or relationships can still trigger
lazy loads, so this module is not guaranteed to be database-free.
"""

from band_access import PUBLIC, effective_visibility


def visible_to_scope(scope, visibility):
    return scope.is_member or visibility == PUBLIC


def belongs_to_scope(scope, item):
    """Return whether a loaded graph node belongs to the URL-resolved band.

    This is intentionally a pure, fail-closed check: a missing ``band_id`` is
    as unsafe as a different one.  Call it before visibility calculation or
    URL signing; legacy/corrupt associations must never influence either.
    """
    return item is not None and getattr(item, 'band_id', None) == scope.band_id


def graph_is_scoped(scope, item):
    """Validate the loaded tenant graph required by a v2 serializer.

    Relationship reads are limited to the graph each response serializes.
    This helper issues no direct query, but reading deferred or expired ORM
    relationships can still trigger a lazy load. Routes eager-load these
    relationships for collections.
    """
    if not belongs_to_scope(scope, item):
        return False
    from models import Media, Rehearsal, Song

    if isinstance(item, Media):
        if not belongs_to_scope(scope, item.song):
            return False
        if item.rehearsal is not None and not belongs_to_scope(scope, item.rehearsal):
            return False
        return all(belongs_to_scope(scope, rehearsal) for rehearsal in item.song.rehearsals)
    if isinstance(item, Song):
        return (all(graph_is_scoped(scope, media) for media in item.media_files)
                and all(belongs_to_scope(scope, rehearsal) for rehearsal in item.rehearsals))
    if isinstance(item, Rehearsal):
        return (all(belongs_to_scope(scope, song) for song in item.songs)
                and all(graph_is_scoped(scope, media) for media in item.media_files))
    return True


def _band_visibility(scope):
    # BandReadScope snapshots the URL-resolved live band's policy at creation,
    # keeping serializers independent of the Band ORM object.
    return getattr(scope, 'band_visibility', PUBLIC)


def song_visibility(scope, song):
    return effective_visibility(scope and _band_visibility(scope), song.visibility)


def rehearsal_visibility(scope, rehearsal):
    return effective_visibility(_band_visibility(scope), rehearsal.visibility)


def media_visibility(scope, media):
    """Include every rehearsal relation that can constrain a media item."""
    if not graph_is_scoped(scope, media):
        raise ValueError('Media graph is outside the resolved band scope.')
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


def personal_log_visibility(scope, log):
    return effective_visibility(_band_visibility(scope), log.visibility)


def gallery_visibility(scope, image):
    return effective_visibility(_band_visibility(scope), image.visibility)


def announcement_visibility(scope, announcement):
    return effective_visibility(_band_visibility(scope), announcement.visibility)


def suggestion_visibility(scope, suggestion):
    return effective_visibility(_band_visibility(scope), suggestion.visibility)
