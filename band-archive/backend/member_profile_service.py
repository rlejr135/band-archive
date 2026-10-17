"""Band-local account profiles; callers serialize mutations on the band row."""
import json

from sqlalchemy import and_, exists, or_

from errors import NotFoundError, ValidationError
from extensions import db
from models import AppUser, BandMembership, Member

POSITION_LABELS = {'VOCAL': '보컬', 'GUITAR': '기타', 'BASS': '베이스',
                   'DRUMS': '드럼', 'KEYBOARD': '키보드', 'OTHER': '기타 역할'}
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
        allowed = {'biography', 'positions', 'custom_position', 'profile_revision'}
        if (set(payload) - allowed or 'profile_revision' not in payload
                or not (set(payload) & (allowed - {'profile_revision'}))):
            raise ValidationError('Profile fields and profile_revision are required.')
        revision = payload['profile_revision']
        if type(revision) is not int or revision < 0:
            raise ValidationError('profile_revision must be a nonnegative integer.')
        if revision != member.profile_revision:
            raise ValidationError('Profile changed; reload before editing.', 409, 'profile_conflict')
        biography = payload.get('biography', member.biography)
        positions = payload.get('positions', json.loads(member.positions_json))
        custom = payload.get('custom_position', member.custom_position)
        if not isinstance(biography, str) or len(biography) > 300 or '\x00' in biography:
            raise ValidationError('biography must be text of at most 300 characters.')
        if (not isinstance(positions, list) or len(positions) > len(POSITION_LABELS)
                or any(not isinstance(p, str) or p not in POSITION_LABELS for p in positions)
                or len(set(positions)) != len(positions)):
            raise ValidationError('positions must contain distinct supported positions.')
        if not isinstance(custom, str) or len(custom) > 50 or '\x00' in custom:
            raise ValidationError('custom_position must be text of at most 50 characters.')
        if 'OTHER' not in positions:
            if 'custom_position' in payload and custom.strip():
                raise ValidationError('custom_position requires OTHER.')
            custom = ''
        positions = [p for p in POSITION_LABELS if p in positions]
        member.biography = biography.strip()
        member.positions_json = json.dumps(positions)
        member.custom_position = custom.strip()
        if 'positions' in payload or 'custom_position' in payload:
            member.instrument = ', '.join(custom.strip() if p == 'OTHER' and custom.strip()
                                          else POSITION_LABELS[p] for p in positions)
        member.profile_revision += 1
        db.session.commit()
    return {**member.to_dict(), **profile_fields(member, actor.id, can_edit=True),
            'can_delete': False, 'can_edit': True, 'can_manage': False}
