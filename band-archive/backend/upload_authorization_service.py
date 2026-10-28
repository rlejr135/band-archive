"""Request-context upload authorization and tenant-target validation.

The authenticated actor is resolved here in the existing order, including after
restriction-cache invalidation when completion is reauthorized. These ORM
queries may acquire locks and refresh account state; they are not pure rules.
Callers retain HTTP authentication entry, capability checks, expiry/recovery
I/O and transaction ownership. No commit, rollback or R2 work is performed here.
"""

from auth_service import current_app_user
from band_service import ACTIVE
from errors import NotFoundError, ValidationError
from extensions import db
from models import AppUser, Band, BandMembership, Member, Rehearsal, Song
from validators import require_int


def active_upload_membership(band_id, *, lock=False):
    """Resolve the current actor only as an active member of this URL band."""
    user = current_app_user(required=True)
    band_query = Band.query.filter(Band.id == band_id, Band.deleted_at.is_(None))
    member_query = BandMembership.query.filter_by(band_id=band_id, user_id=user.id, status=ACTIVE)
    if lock and db.engine.dialect.name != 'sqlite':
        band_query = band_query.with_for_update()
        member_query = member_query.with_for_update()
    band = band_query.one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    membership = member_query.one_or_none()
    if membership is None:
        raise ValidationError('An active band membership is required.', status_code=403)
    return band, membership


def validate_media_upload_target(band_id, data):
    from content_restrictions import require_unrestricted
    song_id = require_int(data.get('song_id'), 'song_id', minimum=1)
    song = Song.query.filter_by(id=song_id, band_id=band_id).one_or_none()
    if song is None:
        raise NotFoundError('Song not found')
    require_unrestricted(song)
    for linked_rehearsal in song.rehearsals:
        if linked_rehearsal.band_id != band_id:
            raise NotFoundError('Song not found')
        require_unrestricted(linked_rehearsal)
    rehearsal_id = data.get('rehearsal_id')
    if rehearsal_id is not None:
        rehearsal_id = require_int(rehearsal_id, 'rehearsal_id', minimum=1)
        rehearsal = Rehearsal.query.filter_by(id=rehearsal_id, band_id=band_id).one_or_none()
        if rehearsal is None:
            raise NotFoundError('Rehearsal not found')
        require_unrestricted(rehearsal)
    return song_id, rehearsal_id


def validate_personal_log_upload_target(band_id, member_id):
    from content_restrictions import require_unrestricted
    member_id = require_int(member_id, 'member_id', minimum=1)
    member = Member.query.filter_by(id=member_id, band_id=band_id).one_or_none()
    if member is None:
        raise NotFoundError('Member not found')
    require_unrestricted(member)
    return member_id


def require_upload_session_owner(band_id, session, membership):
    """Bind completion to its active, verified creator before any R2 work."""
    if (session.band_id != band_id
            or session.created_by_membership_id != membership.id):
        raise NotFoundError('Upload session not found')
    owner = AppUser.query.filter_by(id=membership.user_id).with_for_update().populate_existing().one_or_none()
    if owner is None or owner.status != ACTIVE or owner.email_verified is not True:
        raise ValidationError('An active, verified upload owner is required.', status_code=403)
    member_id = getattr(session, 'member_id', None)
    song_id = getattr(session, 'song_id', None)
    if member_id is not None:
        validate_personal_log_upload_target(band_id, member_id)
    elif song_id is not None:
        validate_media_upload_target(band_id, {'song_id': song_id, 'rehearsal_id': session.rehearsal_id})
    return membership


def reauthorize_upload_session_owner(band_id, session):
    """Recheck creator authority after the completion claim and R2 verify."""
    from content_restrictions import clear_restriction_cache
    # The completing claim committed and released the original band lock.
    # A rights case may have been applied while object I/O was in flight.
    clear_restriction_cache()
    _, membership = active_upload_membership(band_id, lock=True)
    return require_upload_session_owner(band_id, session, membership)

