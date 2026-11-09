import { batchDays, binaryStatus, cellKey, ownDay, PERIODS } from './availabilityState.js';

const canonicalDay = (data, date) => batchDays(data, [cellKey(date, 'morning')], binaryStatus(ownDay(data, date).morning))[0];
const sameValues = (left, right) => PERIODS.every((period) => binaryStatus(left[period]) === binaryStatus(right[period]));
const storedDay = (data, date) => data.days.find((day) => day.membership_id === data.my_membership_id && day.date === date);
// Defaults belong to the editor only; server snapshots and candidate totals stay unchanged.
export const ownDraftDay = (data, date) => storedDay(data, date) ? canonicalDay(data, date) : { date, revision: 0, morning: 'AVAILABLE', afternoon: 'AVAILABLE', evening: 'AVAILABLE' };

export const toggleDraft = (data, drafts, date, period) => {
  const baseline = ownDraftDay(data, date);
  const row = { ...(drafts[date] || baseline) };
  if (!PERIODS.includes(period)) throw new Error('Invalid period');
  row[period] = row[period] === 'AVAILABLE' ? 'UNAVAILABLE' : 'AVAILABLE';
  const next = { ...drafts };
  if (sameValues(row, baseline)) delete next[date]; else next[date] = row;
  return next;
};
export const draftRows = (drafts) => Object.values(drafts).sort((a, b) => a.date.localeCompare(b.date));
export const isDraftChanged = (data, drafts, date, period) => Boolean(drafts[date] && binaryStatus(drafts[date][period]) !== ownDraftDay(data, date)[period]);
export const previewDraft = (data, drafts) => !data ? data : ({ ...data, days: [...data.days.filter((row) => row.membership_id !== data.my_membership_id || !drafts[row.date]), ...draftRows(drafts).map((row) => ({ ...row, membership_id: data.my_membership_id }))] });

export const reconcileDraft = (baseline, drafts, latest) => {
  if (baseline.band_id !== latest.band_id || baseline.my_membership_id !== latest.my_membership_id || baseline.generation !== latest.generation) return 'conflict';
  const rows = draftRows(drafts);
  if (rows.every((row) => storedDay(latest, row.date) && sameValues(row, canonicalDay(latest, row.date)))) return 'confirmed';
  if (rows.every((row) => { const old = canonicalDay(baseline, row.date); const current = canonicalDay(latest, row.date); return Boolean(storedDay(baseline, row.date)) === Boolean(storedDay(latest, row.date)) && old.revision === current.revision && sameValues(old, current); })) return 'unchanged';
  return 'conflict';
};
