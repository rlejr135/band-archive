"""Fail-closed tenant boundary and graph proof for the public v1 API.

The bandless v1 API can only address the one validated import band. The
database cannot make a graph snapshot immutable without stronger transaction
isolation, so this module keeps one immutable root id per request and proves
every traversed edge before serializing, signing, deleting, or mutating.
"""

from flask import current_app, g, request
from sqlalchemy.exc import MultipleResultsFound

from errors import NotFoundError
from extensions import db
from models import TenantScopeUnavailable, validated_legacy_band_id


_BAND_CACHE_KEY = '_legacy_v1_validated_band_id'
_TRUE_VALUES = {'true', '1', 'yes', 'on'}
_FALSE_VALUES = {'false', '0', 'no', 'off'}


class LegacyScopeUnavailable(RuntimeError):
    """The database cannot prove which tenant owns v1 compatibility data."""


def legacy_band_id():
    """Return the one validated import band, immutable for this request."""
    cached = getattr(g, _BAND_CACHE_KEY, None)
    if cached is not None:
        return cached
    try:
        band_id = validated_legacy_band_id(db.session.connection())
    except (TenantScopeUnavailable, MultipleResultsFound) as exc:
        raise LegacyScopeUnavailable('Validated legacy tenant is unavailable.') from exc
    if not isinstance(band_id, int) or isinstance(band_id, bool):
        raise LegacyScopeUnavailable('Validated legacy tenant is unavailable.')
    setattr(g, _BAND_CACHE_KEY, band_id)
    return band_id


def legacy_query(model):
    """Start a query pinned to the request's immutable legacy tenant."""
    if not hasattr(model, 'band_id'):
        raise TypeError(f'{model.__name__} is not tenant-scoped')
    return model.query.filter(model.band_id == legacy_band_id())


def _not_found(message):
    raise NotFoundError(message)


def _same_band_record(model, record_id, band_id):
    if record_id is None:
        return None
    item = model.query.filter(model.id == record_id, model.band_id == band_id).one_or_none()
    if item is None:
        _not_found('Resource not found')
    return item


def _foreign_child(model, foreign_key, record_id, band_id):
    return model.query.filter(
        getattr(model, foreign_key) == record_id, model.band_id != band_id,
    ).first()


def _validate_membership(record, band_id):
    membership_id = getattr(record, 'created_by_membership_id', None)
    if membership_id is None:
        return
    from models import BandMembership
    _same_band_record(BandMembership, membership_id, band_id)


def _validate_graph(record, band_id, seen):
    """Walk all tenant-bound edges reachable from ``record`` declaratively."""
    if record is None or getattr(record, 'band_id', None) != band_id:
        _not_found('Resource not found')
    key = (type(record), record.id)
    if key in seen:
        return
    seen.add(key)
    _validate_membership(record, band_id)

    # Local imports avoid the models -> legacy_scope initialization cycle.
    from models import (
        Comment, Media, MediaVote, Member, PersonalLog,
        PersonalLogMultipartUploadSession, Rehearsal, Song, SongSuggestion,
        SongVote, MultipartUploadSession, rehearsal_songs,
    )

    def children(model, foreign_key):
        if _foreign_child(model, foreign_key, record.id, band_id) is not None:
            _not_found('Resource not found')
        for child in model.query.filter(
                getattr(model, foreign_key) == record.id,
                model.band_id == band_id).all():
            _validate_graph(child, band_id, seen)

    if isinstance(record, Song):
        children(Media, 'song_id')
        children(SongVote, 'song_id')
        rehearsals = Rehearsal.query.join(
            rehearsal_songs, rehearsal_songs.c.rehearsal_id == Rehearsal.id,
        ).filter(rehearsal_songs.c.song_id == record.id).all()
        for rehearsal in rehearsals:
            if rehearsal.band_id != band_id:
                _not_found('Resource not found')
            _validate_graph(rehearsal, band_id, seen)
    elif isinstance(record, Media):
        _validate_graph(_same_band_record(Song, record.song_id, band_id), band_id, seen)
        if record.rehearsal_id is not None:
            _validate_graph(_same_band_record(Rehearsal, record.rehearsal_id, band_id), band_id, seen)
        children(Comment, 'media_id')
        children(MediaVote, 'media_id')
    elif isinstance(record, Rehearsal):
        children(Media, 'rehearsal_id')
        songs = Song.query.join(
            rehearsal_songs, rehearsal_songs.c.song_id == Song.id,
        ).filter(rehearsal_songs.c.rehearsal_id == record.id).all()
        for song in songs:
            if song.band_id != band_id:
                _not_found('Resource not found')
            _validate_graph(song, band_id, seen)
    elif isinstance(record, Member):
        children(PersonalLog, 'member_id')
    elif isinstance(record, PersonalLog):
        _validate_graph(_same_band_record(Member, record.member_id, band_id), band_id, seen)
        children(Comment, 'personal_log_id')
    elif isinstance(record, Comment):
        if record.media_id is not None:
            _validate_graph(_same_band_record(Media, record.media_id, band_id), band_id, seen)
        if record.personal_log_id is not None:
            _validate_graph(_same_band_record(PersonalLog, record.personal_log_id, band_id), band_id, seen)
        if record.parent_id is not None:
            _validate_graph(_same_band_record(Comment, record.parent_id, band_id), band_id, seen)
        children(Comment, 'parent_id')
    elif isinstance(record, SongVote):
        _validate_graph(_same_band_record(Song, record.song_id, band_id), band_id, seen)
    elif isinstance(record, MediaVote):
        _validate_graph(_same_band_record(Media, record.media_id, band_id), band_id, seen)
    elif isinstance(record, MultipartUploadSession):
        _validate_graph(_same_band_record(Song, record.song_id, band_id), band_id, seen)
        if record.rehearsal_id is not None:
            _validate_graph(_same_band_record(Rehearsal, record.rehearsal_id, band_id), band_id, seen)
    elif isinstance(record, PersonalLogMultipartUploadSession):
        _validate_graph(_same_band_record(Member, record.member_id, band_id), band_id, seen)
    elif isinstance(record, SongSuggestion):
        # Counters are denormalized and there is no suggestion-vote relation.
        return


def validate_legacy_graph(record, message='Resource not found'):
    """Fail closed if a record's complete tenant graph is corrupt."""
    try:
        band_id = legacy_band_id()
        # Graph rows may change during a request.  The root tenant id is
        # immutable for compatibility, but relationship proof is deliberately
        # fresh at every boundary rather than caching a partial failed walk.
        _validate_graph(record, band_id, set())
    except NotFoundError:
        _not_found(message)
    return record


def validate_legacy_graphs(records, message='Resource not found'):
    for record in records:
        validate_legacy_graph(record, message)
    return records


def legacy_get_or_404(model, record_id, message=None):
    """Resolve and graph-validate a global-looking v1 id."""
    item = legacy_query(model).filter(model.id == record_id).one_or_none()
    if item is None:
        _not_found(message or f'{model.__name__} not found')
    return validate_legacy_graph(item, message or f'{model.__name__} not found')


def require_legacy_related(parent, related, message='Related resource not found'):
    validate_legacy_graph(parent, message)
    validate_legacy_graph(related, message)
    if getattr(parent, 'band_id', None) != getattr(related, 'band_id', None):
        _not_found(message)
    return related


def reject_foreign_children(parent, child_model, foreign_key, message='Related resource not found'):
    """Compatibility alias; graph validation now proves every child edge."""
    validate_legacy_graph(parent, message)


def legacy_v1_request():
    """Whether the current request is part of the public v1 compatibility API."""
    if request.path == '/health' or request.path.startswith('/api/v2/'):
        return False
    if request.path.startswith('/internal/'):
        return False
    return True


def legacy_writes_enabled(value=None):
    """Parse a deployment override without Python's unsafe string truthiness."""
    value = current_app.config.get('LEGACY_V1_WRITES_ENABLED', True) if value is None else value
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    return False


def reject_legacy_destructive_write():
    """Close legacy destructive routes until they use authenticated v2 policy.

    This is unconditional: the compatibility write flag and legacy tenant
    proof establish neither an authenticated actor nor delete authority.
    """
    from errors import ValidationError
    raise ValidationError('This legacy destructive endpoint is retired; use an authorized v2 action.', 410)
