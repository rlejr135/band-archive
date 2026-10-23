"""Band-local account profiles; callers serialize mutations on the band row."""
import json

from sqlalchemy import and_, exists, or_

from errors import NotFoundError, ValidationError
from extensions import db
from models import AppUser, BandMembership, Member
from join_name import required_join_name
from voc_validation import member_position_values, selected_member_position_values

PROFILE_COLUMNS = ('biography', 'positions_json', 'custom_position', 'profile_revision')


def active_profile_condition():
    """Unlinked historical people remain visible; linked departures do not."""
    return or_(Member.linked_user_id.is_(None), exists().where(and_(
        BandMembership.band_id == Member.band_id,
        BandMembership.user_id == Member.linked_user_id,
        BandMembership.status == 'active',
        AppUser.id == BandMembership.user_id, AppUser.status == 'active',
        AppUser.email_verified.is_(True))))


def profile_has_active_membership(member):
    if member.linked_user_id is None:
        return False
    return db.session.query(BandMembership.id).filter_by(
        band_id=member.band_id, user_id=member.linked_user_id, status='active').first() is not None


def ensure_member_profile(membership):
    """Ensure exactly one linked profile without committing or matching names.

    The caller must already hold the band lock, then the membership lock.
    Database uniqueness is the final backstop against non-service callers.
    """
    if membership.status != 'active':
        raise ValidationError('An active membership is required.', 409)
    profile = Member.query.filter_by(band_id=membership.band_id,
                                    linked_user_id=membership.user_id).with_for_update().one_or_none()
    if profile is not None:
        from deletion_service import is_pending_deletion
        if is_pending_deletion('member', profile.id, profile.band_id):
            raise ValidationError('Profile deletion must be resolved before joining.', 409)
        return profile
    user = db.session.get(AppUser, membership.user_id)
    # An email is never a display-name fallback (including malformed imported names).
    name = membership.requested_name
    if name is None:
        name = (user.display_name or '').strip() if user else ''
        if not name or '@' in name:
            name = '멤버'
    profile = Member(band_id=membership.band_id, linked_user_id=membership.user_id,
                     name=name[:100], instrument='', biography='', positions_json='[]',
                     custom_position='', profile_revision=0)
    # Private ORM-event context: only the membership-backed service can opt
    # this insert into the authenticated v2 tenant path. The model hook
    # independently verifies the active, verified membership before bypassing
    # legacy-import scoping.
    profile._trusted_profile_membership_id = membership.id
    db.session.add(profile)
    db.session.flush()
    return profile


def activate_member_profile(membership, submitted_name=None, positions=None, custom_position=''):
    """Ensure the linked profile and apply requested name/positions atomically.

    Call only while transitioning a non-active membership to active. Routine
    profile ensures and active-member retries must never rename an existing
    band-local profile. An omitted/empty position selection leaves an existing
    profile untouched; a new profile retains the blank defaults from ensure.
    """
    # Validate before ensuring a linked profile or applying the name. Empty
    # selections deliberately mean "no change" on activation/rejoin; only
    # own_profile's explicit edit path treats [] as a clear operation.
    position_values = selected_member_position_values(positions, custom_position)
    profile = ensure_member_profile(membership)
    changed = False
    if submitted_name is not None and profile.name != submitted_name:
        profile.name = submitted_name[:100]
        changed = True
    if position_values:
        if (profile.positions_json != position_values['positions_json']
                or profile.custom_position != position_values['custom_position']
                or profile.instrument != position_values['instrument']):
            profile.positions_json = position_values['positions_json']
            profile.custom_position = position_values['custom_position']
            profile.instrument = position_values['instrument']
            changed = True
    if changed:
        profile.profile_revision += 1
    return profile


def profile_fields(member, user_id=None, *, can_edit=False):
    return {'biography': member.biography, 'positions': json.loads(member.positions_json),
            'custom_position': member.custom_position, 'profile_revision': member.profile_revision,
            'is_self': user_id is not None and member.linked_user_id == user_id,
            'can_edit_profile': bool(can_edit and member.linked_user_id == user_id),
            'membership_status': ('legacy' if member.linked_user_id is None else
                                  getattr(member, '_profile_membership_status', 'active'))}


def own_profile(actor, band_id, payload=None):
    from band_service import _begin_privileged_mutation, require_band_role
    if actor.status != 'active' or actor.email_verified is not True:
        raise ValidationError('An active verified account is required.', 403)
    if payload is not None:
        _begin_privileged_mutation()
    _band, membership = require_band_role(actor, band_id, {'owner', 'admin', 'member'},
                                          lock=payload is not None)
    query = Member.query.filter_by(band_id=band_id, linked_user_id=membership.user_id)
    member = (query.with_for_update().populate_existing() if payload is not None else query).one_or_none()
    if member is None:
        raise NotFoundError('Member profile not found')
    from deletion_service import is_pending_deletion
    if is_pending_deletion('member', member.id, band_id):
        raise NotFoundError('Member profile not found')
    if payload is not None:
        allowed = {'name', 'biography', 'positions', 'custom_position', 'profile_revision'}
        if (set(payload) - allowed or 'profile_revision' not in payload
                or not (set(payload) & (allowed - {'profile_revision'}))):
            raise ValidationError('Profile fields and profile_revision are required.')
        revision = payload['profile_revision']
        if type(revision) is not int or revision < 0:
            raise ValidationError('profile_revision must be a nonnegative integer.')
        if revision != member.profile_revision:
            raise ValidationError('Profile changed; reload before editing.', 409, 'profile_conflict')
        name = required_join_name(payload['name']) if 'name' in payload else member.name
        biography = payload.get('biography', member.biography)
        positions = payload.get('positions', json.loads(member.positions_json))
        custom = payload.get('custom_position', member.custom_position)
        if 'positions' in payload and 'custom_position' not in payload and isinstance(positions, list) \
                and 'OTHER' not in positions:
            custom = ''
        if not isinstance(biography, str) or len(biography) > 300 or '\x00' in biography:
            raise ValidationError('biography must be text of at most 300 characters.')
        position_values = member_position_values(positions, custom)
        member.name = name
        member.biography = biography.strip()
        member.positions_json = position_values['positions_json']
        member.custom_position = position_values['custom_position']
        if 'positions' in payload or 'custom_position' in payload:
            member.instrument = position_values['instrument']
        member.profile_revision += 1
        db.session.commit()
    return {**member.to_dict(), **profile_fields(member, actor.id, can_edit=True),
            'can_delete': False, 'can_edit': True, 'can_manage': False}
