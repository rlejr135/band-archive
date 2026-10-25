"""SQL-only predicates shared by band content reads and writes."""

from sqlalchemy import and_, exists, or_

from models import BandMembership, R2DeletionTask


def _not_pending_delete(entity, resource_type):
    return ~exists().where(and_(R2DeletionTask.band_id == entity.band_id,
                                R2DeletionTask.resource_type == resource_type,
                                R2DeletionTask.resource_id == entity.id,
                                R2DeletionTask.status != 'completed'))


def _creator_is_scoped_sql(entity, scope):
    """Reject a corrupt cross-band creator pointer without rejecting legacy NULLs."""
    creator_id = getattr(entity, 'created_by_membership_id', None)
    if creator_id is None:
        return True
    return or_(
        creator_id.is_(None),
        exists().where(and_(BandMembership.id == creator_id,
                            BandMembership.band_id == scope.band_id)),
    )
