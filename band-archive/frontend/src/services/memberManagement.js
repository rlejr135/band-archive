export const membershipName = (membership) => membership.member_name || membership.user?.display_name || '멤버';
