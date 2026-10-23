export const MEMBER_POSITIONS = Object.freeze([
  ['VOCAL', '보컬'], ['GUITAR', '기타'], ['BASS', '베이스'],
  ['DRUMS', '드럼'], ['KEYBOARD', '키보드'], ['OTHER', '기타 악기·역할'],
]);

export const toggleMemberPosition = (currentPositions, key) => {
  const positions = Array.isArray(currentPositions) ? currentPositions : [];
  return positions.includes(key)
    ? positions.filter((position) => position !== key)
    : [...positions, key];
};

export const memberPositionLabels = (positions, customPosition = '') => {
  const selected = Array.isArray(positions) ? positions : [];
  return MEMBER_POSITIONS
    .filter(([key]) => selected.includes(key))
    .map(([key, label]) => key === 'OTHER' && customPosition ? customPosition : label);
};
