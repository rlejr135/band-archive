"""Atomic selection of a band's FeaturedGalleryImage."""

from sqlalchemy import text

from auth_service import current_app_user
from band_service import MANAGER_ROLES, audit
from comment_rules import _owned_or_404
from content_write_scope import active_member_write_scope
from content_write_values import VISIBILITIES as _VISIBILITIES
from errors import NotFoundError, ValidationError
from extensions import db
from models import BandMembership, GalleryImage


def _scope_for_write(band_id, *, lock=False, lock_actor=False):
    user = current_app_user(required=True)
    return active_member_write_scope(band_id, user, lock=lock, lock_actor=lock_actor)


def _run_featured_gallery_write(operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def set_gallery_featured(*, band_id, image_id, desired, prepare_response):
    """Select or clear an image; response errors roll back selection and audit."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        if membership.role not in MANAGER_ROLES:
            raise ValidationError('Insufficient band role.', 403)

        image = _owned_or_404(
            GalleryImage, image_id, band.id, 'Gallery image not found', lock=True,
        )
        # A NULL creator is a valid legacy row. Any non-NULL creator pointer
        # must remain in this tenant before the image can be changed or signed.
        if image.created_by_membership_id is not None:
            creator = BandMembership.query.filter_by(
                id=image.created_by_membership_id, band_id=band.id,
            ).one_or_none()
            if creator is None:
                raise NotFoundError('Gallery image not found')
        if image.visibility not in _VISIBILITIES:
            raise NotFoundError('Gallery image not found')

        featured_query = GalleryImage.query.filter(
            GalleryImage.band_id == band.id,
            GalleryImage.is_featured.is_(True),
        )
        if db.engine.dialect.name != 'sqlite':
            featured_query = featured_query.with_for_update()
        featured_rows = featured_query.all()
        before = bool(image.is_featured)
        changed = False
        cleared = 0
        if desired:
            other_featured = featured_query.filter(GalleryImage.id != image.id)
            cleared = sum(existing.id != image.id for existing in featured_rows)
            if cleared:
                # Apply the clears before setting the selected row so the
                # partial unique index remains satisfied in either ID order.
                other_featured.update({GalleryImage.is_featured: False}, synchronize_session='fetch')
                changed = True
            if not before:
                image.is_featured = True
                changed = True
        elif before:
            image.is_featured = False
            changed = True

        if changed:
            audit('content.gallery.featured.changed', user, band, image,
                  {'is_featured': desired, 'cleared_count': cleared})
        return prepare_response(scope, image)

    return _run_featured_gallery_write(operation)
