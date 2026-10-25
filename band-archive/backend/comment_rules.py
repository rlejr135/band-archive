"""Comment-tree safety shared by band and platform deletion paths.

The generic band-owned lookup lives here because the tree guard uses it to
resolve comments without importing route code; content writes also reuses that
lookup for other owned resources.
"""

from errors import NotFoundError
from extensions import db
from models import (
    Announcement, Comment, GalleryImage, Media, Member, PersonalLog, R2DeletionTask,
    Rehearsal, Song, SongSuggestion,
)


def _owned_or_404(model, record_id, band_id, message='Resource not found', *, lock=False):
    query = model.query.filter(model.id == record_id, model.band_id == band_id)
    resource_type = {
        Media: 'media', PersonalLog: 'personal_log', GalleryImage: 'gallery',
        Song: 'song', Rehearsal: 'rehearsal', Member: 'member', Comment: 'comment',
        SongSuggestion: 'suggestion', Announcement: 'announcement',
    }.get(model)
    if resource_type:
        query = query.filter(~db.session.query(R2DeletionTask.id).filter(
            R2DeletionTask.band_id == band_id,
            R2DeletionTask.resource_type == resource_type,
            R2DeletionTask.resource_id == model.id,
            R2DeletionTask.status != 'completed',
        ).exists())
    if lock and db.engine.dialect.name != 'sqlite':
        query = query.with_for_update()
    item = query.one_or_none()
    if item is None:
        raise NotFoundError(message)
    return item


def _comment_target_kind(comment):
    if comment.media_id is not None and comment.personal_log_id is None:
        return 'media', comment.media_id
    if comment.personal_log_id is not None and comment.media_id is None:
        return 'personal_log', comment.personal_log_id
    raise NotFoundError('Resource not found')


def _assert_comment_tree_safe(comment):
    """Validate every cascade descendant before a delete touches the tree."""
    kind, target_id = _comment_target_kind(comment)
    pending, seen = [comment.id], set()
    while pending:
        node_id = pending.pop()
        if node_id in seen:
            raise NotFoundError('Resource not found')
        seen.add(node_id)
        node = _owned_or_404(Comment, node_id, comment.band_id, 'Resource not found')
        if _comment_target_kind(node) != (kind, target_id):
            raise NotFoundError('Resource not found')
        child_ids = [row[0] for row in db.session.query(Comment.id).filter(Comment.parent_id == node.id).all()]
        pending.extend(child_ids)
