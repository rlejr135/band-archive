"""Atomic account-owned comments and replies on scoped band content."""

import math

from sqlalchemy import text

from auth_service import current_app_user
from band_access import content_capabilities
from band_service import audit
from comment_rules import (
    _assert_comment_tree_safe, _comment_target_kind, _owned_or_404,
    _target_for_comment,
)
from content_write_rules import can_change_authored_content as _can_change
from content_write_scope import active_member_write_scope
from content_write_values import text_value as _text
from errors import NotFoundError, ValidationError
from extensions import db
from models import Comment


def _scope_for_write(band_id, *, lock=False, lock_actor=False):
    user = current_app_user(required=True)
    return active_member_write_scope(band_id, user, lock=lock, lock_actor=lock_actor)


def _run_comment_write(operation):
    try:
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def _comment_parent_or_404(parent_id, kind, target_id, band_id):
    if parent_id is None:
        return None
    if isinstance(parent_id, bool) or not isinstance(parent_id, int):
        raise ValidationError('parent_id must be an integer.')
    parent = _owned_or_404(Comment, parent_id, band_id, 'Resource not found')
    if _comment_target_kind(parent) != (kind, target_id):
        raise NotFoundError('Resource not found')
    # Existing malformed loops must never become a v2 thread parent.
    seen, cursor = set(), parent
    while cursor is not None:
        if cursor.id in seen:
            raise NotFoundError('Resource not found')
        seen.add(cursor.id)
        cursor = _owned_or_404(Comment, cursor.parent_id, band_id, 'Resource not found') if cursor.parent_id else None
    return parent


def _timestamp(data, *, parent, target):
    if 'timestamp_seconds' not in data:
        return None, False
    value = data['timestamp_seconds']
    if value is None:
        return None, True
    if parent is not None or getattr(target, 'file_type', None) not in {'audio', 'video'}:
        raise ValidationError('Timestamps are only available for top-level audio or video comments.')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        raise ValidationError('timestamp_seconds must be a non-negative finite number.')
    return float(value), True


def _author(user):
    # Display name is account metadata, not request-supplied impersonation.
    return (user.display_name or user.email).strip()[:50] or 'Member'


def _load_comment_and_target(comment_id, band_id, *, lock=False):
    comment = _owned_or_404(Comment, comment_id, band_id, 'Comment not found', lock=lock)
    kind, target_id = _comment_target_kind(comment)
    target = _target_for_comment(kind, target_id, band_id, lock=lock)
    return comment, kind, target


def create_comment(*, band_id, kind, target_id, data, prepare_response):
    """Create a comment or reply; response errors roll back its audit too."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        target = _target_for_comment(kind, target_id, band.id, lock=True)
        parent_id = data.get('parent_id')
        parent = _comment_parent_or_404(parent_id, kind, target.id, band.id)
        timestamp_seconds, _ = _timestamp(data, parent=parent, target=target)
        content = _text(data.get('content'), 'content', 10000, required=True)
        values = {'band_id': band.id, 'created_by_membership_id': membership.id, 'is_legacy': False,
                  'parent_id': parent.id if parent else None, 'author': _author(user), 'content': content,
                  'timestamp_seconds': timestamp_seconds}
        values['media_id' if kind == 'media' else 'personal_log_id'] = target.id
        comment = Comment(**values)
        db.session.add(comment); db.session.flush()
        audit('content.comment.created', user, band, comment, {'target': kind, 'target_id': target.id})
        return prepare_response(scope, comment, target)
    return _run_comment_write(operation)


def update_comment(*, band_id, comment_id, data, prepare_response):
    """Edit account-owned text and playback position in one transaction."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True)
        comment, kind, target = _load_comment_and_target(comment_id, band.id, lock=True)
        if not _can_change(comment, membership):
            raise ValidationError('You may only edit your own content.', 403)
        if 'content' in data:
            comment.content = _text(data['content'], 'content', 10000, required=True)
        if 'timestamp_seconds' in data:
            comment.timestamp_seconds, _ = _timestamp(data, parent=comment.parent, target=target)
        audit('content.comment.updated', user, band, comment, {'target': kind})
        return prepare_response(scope, comment, target)
    return _run_comment_write(operation)


def delete_comment(*, band_id, comment_id, prepare_response):
    """Validate the cascade tree and delete it with an atomic audit."""
    def operation():
        scope, band, membership, user = _scope_for_write(band_id, lock=True, lock_actor=True)
        comment, kind, target = _load_comment_and_target(comment_id, band.id, lock=True)
        if not content_capabilities(scope, comment)['can_delete']:
            raise ValidationError('Only an active verified band owner or platform administrator may delete content.', 403)
        _assert_comment_tree_safe(comment)
        audit('content.comment.deleted', user, band, comment, {'target': kind, 'target_id': target.id})
        db.session.delete(comment)
        return prepare_response(comment_id)
    return _run_comment_write(operation)
