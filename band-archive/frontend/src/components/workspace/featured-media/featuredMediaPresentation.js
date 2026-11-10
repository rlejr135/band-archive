export const mergeUniqueEntries = (previous, next) => {
  const updates = new Map(next.map((entry) => [String(entry.id), entry]));
  const merged = previous.map((entry) => updates.get(String(entry.id)) || entry);
  const known = new Set(merged.map((entry) => String(entry.id)));
  next.forEach((entry) => { if (!known.has(String(entry.id))) { known.add(String(entry.id)); merged.push(entry); } });
  return merged;
};

export const formatRepresentativeDate = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '등록일 정보 없음' : new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'Asia/Seoul' }).format(date);
};
