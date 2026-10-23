export const REHEARSAL_TYPES = Object.freeze([
  ['REGULAR', '정기합주'],
  ['EXTRA', '추가합주'],
  ['LIVE', '라이브공연'],
  ['OTHER', '기타'],
  ['UNCLASSIFIED', '미분류'],
]);

const LABELS = new Map(REHEARSAL_TYPES);

export const normalizeRehearsalType = (value) => LABELS.has(value) ? value : 'UNCLASSIFIED';
export const rehearsalTypeLabel = (value) => LABELS.get(normalizeRehearsalType(value));
