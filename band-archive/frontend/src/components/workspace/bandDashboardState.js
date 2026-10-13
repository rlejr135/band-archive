export const DASHBOARD_PAGE_SIZE = 25;

export const strictPositiveId = (value) => {
  if (typeof value !== 'string' || !/^[1-9]\d*$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
};

export const isoMonthFromDate = (value) => {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) return null;
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}`;
};

export const parseIsoDate = (value) => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null;
  return date;
};

export const rehearsalOccursOnDate = (rehearsal, dateIso) => {
  if (!parseIsoDate(dateIso) || !rehearsal || typeof rehearsal !== 'object') return false;
  const canonical = parseIsoDate(rehearsal.date) ? rehearsal.date : null;
  if (canonical === dateIso) return true;
  const start = parseIsoDate(rehearsal.start_date) ? rehearsal.start_date : null;
  const end = parseIsoDate(rehearsal.end_date) ? rehearsal.end_date : null;
  return Boolean(start && end && start <= end && dateIso >= start && dateIso <= end);
};

export const rehearsalsForDate = (items, dateIso) => (
  Array.isArray(items) ? items.filter((item) => rehearsalOccursOnDate(item, dateIso)) : []
);

const mergeItems = (previousItems, incomingItems) => {
  const merged = [...(Array.isArray(previousItems) ? previousItems : [])];
  const positions = new Map(merged.map((item, index) => [String(item.id), index]));
  for (const item of Array.isArray(incomingItems) ? incomingItems : []) {
    const key = String(item.id);
    if (positions.has(key)) merged[positions.get(key)] = item;
    else {
      positions.set(key, merged.length);
      merged.push(item);
    }
  }
  return merged;
};

export const mergeDashboardPage = (previous, incoming, { append = false } = {}) => {
  if (!previous || !append) return incoming;
  const previousPage = previous.month_rehearsals || {};
  const incomingPage = incoming.month_rehearsals || {};
  const items = mergeItems(previousPage.items, incomingPage.items);
  const pagination = incomingPage.pagination || previousPage.pagination || {};
  return {
    ...incoming,
    month_rehearsals: {
      ...incomingPage,
      items,
      pagination,
    },
  };
};

export const mergeDashboardImages = (previous, incoming) => {
  if (!previous || previous.month !== incoming?.month) return previous;
  return {
    ...previous,
    featured_gallery: incoming.featured_gallery ?? null,
    recent_gallery: Array.isArray(incoming.recent_gallery) ? incoming.recent_gallery : previous.recent_gallery,
  };
};
