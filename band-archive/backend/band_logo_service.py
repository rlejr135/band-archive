"""Atomic BandLogo selection, retaining its fail-closed URL preparation."""

from sqlalchemy import text

from band_access import BandReadScope, MembershipAudience, RequestAudience
from band_content_predicates import _creator_is_scoped_sql, _not_pending_delete
from band_service import audit
from deletion_service import actor_is_eligible, load_current_actor, lock_band_and_membership
from errors import NotFoundError
from extensions import db
from models import BandLogo, GalleryImage


def _run_logo_write(operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def set_band_logo(*, band_id, actor, image_id, prepare_logo):
    """Commit a selection before the route creates JSON.

    The route resolves ``actor`` before entering this transaction. URL
    preparation failures retain the chosen id with a null URL; they do not
    undo the selection. Database failures still roll back selection and audit.
    """
    user = actor

    def write():
        band, membership = lock_band_and_membership(band_id, user.id, require_member=False)
        actor = load_current_actor(user.id, lock=True)
        if band is None or actor is None or not actor_is_eligible(actor):
            raise NotFoundError('Resource not found')
        is_admin = actor.is_platform_admin is True
        is_owner = membership is not None and membership.role == 'owner'
        if not (is_owner or is_admin):
            raise NotFoundError('Resource not found')
        member_audience = MembershipAudience(
            id=membership.id, band_id=membership.band_id, role=membership.role, status=membership.status,
            user_subject=actor.supabase_user_id, user_display_name=actor.display_name, user_email=actor.email,
        ) if membership else None
        audience = RequestAudience(
            user_id=actor.id, subject=actor.supabase_user_id, account_active=True,
            email_verified=True, is_platform_admin=is_admin,
        )
        scope = BandReadScope(
            band_id=band.id, band_slug=band.slug, audience=audience, membership=member_audience,
            band_visibility=band.default_visibility,
        )
        image = None
        if image_id is not None:
            image_query = GalleryImage.query.filter(
                GalleryImage.id == image_id, GalleryImage.band_id == band.id,
                _not_pending_delete(GalleryImage, 'gallery'), _creator_is_scoped_sql(GalleryImage, scope),
            )
            if db.engine.dialect.name == 'postgresql':
                image_query = image_query.with_for_update()
            image = image_query.one_or_none()
            if image is None:
                raise NotFoundError('Resource not found')

        logo = BandLogo.query.filter_by(band_id=band.id)
        if db.engine.dialect.name == 'postgresql':
            logo = logo.with_for_update()
        logo = logo.one_or_none()
        previous_id = logo.gallery_image_id if logo else None
        if image_id is None:
            if logo is not None:
                db.session.delete(logo)
        elif logo is None:
            db.session.add(BandLogo(band_id=band.id, gallery_image_id=image_id))
        elif logo.gallery_image_id != image_id:
            logo.gallery_image_id = image_id
        db.session.flush()
        if previous_id != image_id:
            audit('band_logo_updated', actor, band=band, target=band,
                  details={'gallery_image_id': image_id, 'previous_gallery_image_id': previous_id})
        serialized = None
        if image_id is not None:
            try:
                value = prepare_logo(scope, image) if image is not None else None
                serialized = {'gallery_image_id': image_id,
                              'url': value.get('url') if value else None}
            except Exception:
                serialized = {'gallery_image_id': image_id, 'url': None}
        return {'logo': serialized}

    return _run_logo_write(write)
