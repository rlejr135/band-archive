"""Authored-content editing rule shared by songs, rehearsals and comments."""

from band_service import MANAGER_ROLES


def can_change_authored_content(item, membership):
    return membership.role in MANAGER_ROLES or (
        item.created_by_membership_id is not None and item.created_by_membership_id == membership.id
    )
