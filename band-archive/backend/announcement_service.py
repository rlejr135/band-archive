"""Atomic creation, editing and clearing of a band's singleton announcement."""

from sqlalchemy import text

from auth_service import current_app_user
from band_service import MANAGER_ROLES, audit
from content_write_scope import active_member_write_scope
from content_write_values import text_value as _text, visibility_value as _visibility
from errors import NotFoundError, ValidationError
from extensions import db
from models import Announcement
from policy_service import confirm_public_expansion


ANNOUNCEMENT_FIELDS = {'content', 'visibility', 'visibility_confirm'}


def _scope_for_write(band_id, *, lock=False, lock_actor=False):
    user = current_app_user(required=True)
    return active_member_write_scope(band_id, user, lock=lock, lock_actor=lock_actor)


def _run_announcement_write(operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def put_announcement(*, band_id, data, prepare_response):
    """Create or update the singleton and audit; prepare its response before commit."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        if membership.role not in MANAGER_ROLES:
            raise ValidationError('Insufficient band role.', 403)
        query = Announcement.query.filter_by(band_id=band.id)
        if db.engine.dialect.name != 'sqlite':
            query = query.with_for_update()
        rows = query.order_by(Announcement.id.asc()).limit(2).all()
        if len(rows) > 1:
            raise NotFoundError('Announcement not found')
        announcement = rows[0] if rows else Announcement(
            band_id=band.id, created_by_membership_id=membership.id,
            visibility=_visibility(data.get('visibility'), band.default_visibility),
        )
        if 'content' not in data:
            raise ValidationError('content is required.')
        if rows and band.default_visibility == 'PUBLIC' and announcement.visibility == 'MEMBERS' and data.get('visibility') == 'PUBLIC':
            confirm_public_expansion(data, user, band, announcement)
        announcement.content = _text(data['content'], 'content', 10000, required=True)
        if 'visibility' in data:
            announcement.visibility = _visibility(data['visibility'], announcement.visibility)
        if not rows:
            db.session.add(announcement); db.session.flush()
            audit('content.announcement.created', user, band, announcement,
                  {'visibility': announcement.visibility})
        else:
            audit('content.announcement.updated', user, band, announcement,
                  {'fields': sorted(set(data) & ANNOUNCEMENT_FIELDS)})
        return prepare_response(scope, announcement)
    return _run_announcement_write(operation)


def clear_announcement(*, band_id, prepare_response):
    """Clear text while retaining singleton identity, author and visibility."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        if membership.role != 'owner' and user.is_platform_admin is not True:
            raise ValidationError('Only an active verified owner may clear the announcement.', 403)
        rows = Announcement.query.filter_by(band_id=band.id).order_by(Announcement.id.asc()).limit(2).all()
        if len(rows) != 1:
            raise NotFoundError('Announcement not found')
        announcement = rows[0]
        announcement.content = ''
        audit('content.announcement.cleared', user, band, announcement)
        return prepare_response(scope, announcement)
    return _run_announcement_write(operation)
