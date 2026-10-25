"""Active membership scope for authenticated v2 content writes."""

from band_access import BandReadScope, MembershipAudience, RequestAudience
from band_service import ACTIVE
from errors import NotFoundError, ValidationError
from extensions import db
from models import AppUser, Band, BandMembership
from v2_content_serializers import scope_with_band_visibility


def active_member_write_scope(band_id, actor, *, lock=False, lock_actor=False):
    """Recheck a server-authenticated actor's active band membership.

    Callers needing SQLite write serialization must acquire BEGIN IMMEDIATE
    before entering here; actor may be expired by authentication's commit.
    """
    band_query = Band.query.filter(Band.id == band_id, Band.deleted_at.is_(None))
    membership_query = BandMembership.query.filter_by(band_id=band_id, user_id=actor.id, status=ACTIVE)
    if lock and db.engine.dialect.name != 'sqlite':
        band_query = band_query.with_for_update()
        membership_query = membership_query.with_for_update()
    band = band_query.one_or_none()
    if band is None:
        raise NotFoundError('Band not found')
    membership = membership_query.one_or_none()
    if membership is None:
        raise ValidationError('An active band membership is required.', 403)
    if lock_actor:
        actor_query = AppUser.query.filter_by(id=actor.id)
        if lock and db.engine.dialect.name != 'sqlite':
            actor_query = actor_query.with_for_update()
        actor = actor_query.populate_existing().one_or_none()
        if actor is None:
            raise ValidationError('An active band membership is required.', 403)
    audience = RequestAudience(
        user_id=actor.id, subject=actor.supabase_user_id,
        account_active=actor.status == ACTIVE,
        email_verified=actor.email_verified is True,
        is_platform_admin=actor.is_platform_admin is True,
    )
    member_audience = MembershipAudience(
        id=membership.id, band_id=membership.band_id, role=membership.role, status=membership.status,
        user_subject=actor.supabase_user_id, user_display_name=actor.display_name, user_email=actor.email,
    )
    return scope_with_band_visibility(BandReadScope(
        band_id=band.id, band_slug=band.slug, audience=audience, membership=member_audience,
    ), band), band, membership, actor
