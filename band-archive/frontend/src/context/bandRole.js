export const bandRoleLabel = (role, { isMember = true } = {}) => {
  if (!isMember) return '방문자';
  if (role === 'owner') return '소유자';
  if (role === 'admin') return '관리자';
  return '멤버';
};
