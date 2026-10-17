export const PERIODS = ['morning', 'afternoon', 'evening'];
export const availabilityPath = (bandId, from, to) => `/api/v2/bands/${Number(bandId)}/availability?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;
export const PERIOD_LABELS = { morning: '오전', afternoon: '오후', evening: '저녁' };
export const STATUS_LABELS = { AVAILABLE: '가능', UNAVAILABLE: '불가', UNKNOWN: '미정' };
export const activeAvailabilityMember = (band) => Boolean(band.membership && String(band.membership.status || 'active').toLowerCase() === 'active');
export const seoulToday = () => {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map((type) => parts.find((part) => part.type === type).value).join('-');
};
export const validDay = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value || '') && Number.isFinite(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
export const dateRange = (from, to) => {
  if (!validDay(from) || !validDay(to) || from > to) return [];
  const result = []; let day = new Date(`${from}T00:00:00Z`);
  while (day.toISOString().slice(0, 10) <= to && result.length <= 42) { result.push(day.toISOString().slice(0, 10)); day = new Date(day.getTime() + 86400000); }
  return result.length > 42 ? [] : result;
};
export const monthDays = (month) => {
  if (!/^\d{4}-\d{2}$/.test(month || '') || !validDay(`${month}-01`)) return [];
  const first = new Date(`${month}-01T00:00:00Z`); const end = new Date(first); end.setUTCMonth(first.getUTCMonth() + 1, 0);
  return dateRange(`${month}-01`, end.toISOString().slice(0, 10));
};
export const calendarWeeks = (month) => {
  const dates = monthDays(month); if (!dates.length) return [];
  const offset = new Date(`${dates[0]}T00:00:00Z`).getUTCDay();
  const cells = [...Array(offset).fill(null), ...dates];
  while (cells.length % 7) cells.push(null);
  return Array.from({ length: cells.length / 7 }, (_, i) => cells.slice(i * 7, i * 7 + 7));
};
export const inclusiveDates = (dates, anchor, target) => {
  const a = dates.indexOf(anchor); const b = dates.indexOf(target);
  return a < 0 || b < 0 ? [] : dates.slice(Math.min(a, b), Math.max(a, b) + 1);
};
export const adjacentMonth = (month, offset) => {
  const day = new Date(`${month}-01T00:00:00Z`); day.setUTCMonth(day.getUTCMonth() + offset); return day.toISOString().slice(0, 7);
};
export const cellKey = (date, period) => `${date}:${period}`;
export const ownDay = (data, date) => data.days.find((day) => day.membership_id === data.my_membership_id && day.date === date) || { date, morning: 'UNKNOWN', afternoon: 'UNKNOWN', evening: 'UNKNOWN', revision: 0 };
export const batchDays = (data, cells, status) => {
  if (!Object.hasOwn(STATUS_LABELS, status)) throw new Error('Invalid status');
  const rows = new Map();
  for (const key of cells) {
    const [date, period] = key.split(':'); if (!validDay(date) || !PERIODS.includes(period)) throw new Error('Invalid cell');
    if (!rows.has(date)) { const source = ownDay(data, date); rows.set(date, { date, revision: source.revision, ...Object.fromEntries(PERIODS.map((name) => [name, source[name]])) }); }
    rows.get(date)[period] = status;
  }
  if (!rows.size || rows.size > 42) throw new Error('Invalid range');
  return [...rows.values()].sort((a, b) => a.date.localeCompare(b.date));
};
export const candidates = (data, dates, selected, today = null) => {
  const ids = [...new Set(selected)].filter((id) => data.members.some((member) => member.membership_id === id));
  if (!ids.length) return [];
  const index = new Map(data.days.map((day) => [`${day.membership_id}:${day.date}`, day]));
  return dates.filter((date) => !today || date >= today).flatMap((date) => PERIODS.map((period, order) => {
    const statuses = ids.map((id) => index.get(`${id}:${date}`)?.[period] || 'UNKNOWN');
    const available = statuses.filter((value) => value === 'AVAILABLE').length; const unavailable = statuses.filter((value) => value === 'UNAVAILABLE').length;
    return { date, period, order, available, unavailable, unknown: ids.length - available - unavailable, all: available === ids.length, total: ids.length };
  })).sort((a, b) => Number(b.all) - Number(a.all) || b.available - a.available || a.date.localeCompare(b.date) || a.order - b.order);
};
// A compact, deterministic change token, not a permission token or personal-data cache.
export const availabilityFingerprint = (data, date) => JSON.stringify([data.generation, data.members.map((member) => member.membership_id).sort((a, b) => a - b), data.days.filter((day) => day.date === date).map((day) => [day.membership_id, day.revision]).sort((a, b) => a[0] - b[0])]);
