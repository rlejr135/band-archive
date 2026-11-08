import { batchDays, binaryStatus, cellKey, ownDay, PERIODS } from './availabilityState.js';

const canonicalDay = (data, date) => batchDays(data, [cellKey(date, 'morning')], binaryStatus(ownDay(data, date).morning))[0];
const sameValues = (left, right) => PERIODS.every((period) => binaryStatus(left[period]) === binaryStatus(right[period]));

export const toggleDraft = (data, drafts, date, period) => {
  const baseline = canonicalDay(data, date);
  const row = { ...(drafts[date] || baseline) };
  if (!PERIODS.includes(period)) throw new Error('Invalid period');
  row[period] = row[period] === 'AVAILABLE' ? 'UNAVAILABLE' : 'AVAILABLE';
  const next = { ...drafts };
  if (sameValues(row, baseline)) delete next[date]; else next[date] = row;
  return next;
};
export const draftRows = (drafts) => Object.values(drafts).sort((a, b) => a.date.localeCompare(b.date));
export const previewDraft = (data, drafts) => !data ? data : ({ ...data, days: [...data.days.filter((row) => row.membership_id !== data.my_membership_id || !drafts[row.date]), ...draftRows(drafts).map((row) => ({ ...row, membership_id: data.my_membership_id }))] });

export const reconcileDraft = (baseline, drafts, latest) => {
  if (baseline.band_id !== latest.band_id || baseline.my_membership_id !== latest.my_membership_id || baseline.generation !== latest.generation) return 'conflict';
  const rows = draftRows(drafts);
  if (rows.every((row) => sameValues(row, canonicalDay(latest, row.date)))) return 'confirmed';
  if (rows.every((row) => { const old = canonicalDay(baseline, row.date); const current = canonicalDay(latest, row.date); return old.revision === current.revision && sameValues(old, current); })) return 'unchanged';
  return 'conflict';
};
