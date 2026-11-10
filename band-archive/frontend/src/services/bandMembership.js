// `/api/v2/bands` omits status for active memberships in its current DTO.
// This predicate applies only to band.membership, not member profiles or capabilities.
export const isActiveBandMembership = (membership) => Boolean(
  membership && typeof membership === 'object'
  && (membership.status === undefined
    || (typeof membership.status === 'string' && membership.status.toLowerCase() === 'active'))
);
