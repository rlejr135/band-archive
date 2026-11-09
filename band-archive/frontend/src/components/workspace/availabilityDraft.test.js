import assert from 'node:assert/strict';
import test from 'node:test';
import { draftRows, isDraftChanged, ownDraftDay, previewDraft, reconcileDraft, toggleDraft } from './availabilityDraft.js';
const data = { band_id: 1, generation: 2, my_membership_id: 7, members: [{ membership_id: 7 }], days: [{ membership_id: 7, date: '2026-11-08', revision: 4, morning: 'UNKNOWN', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE' }] };
test('draft toggles preserve original revisions, remove reverted days and never change baseline', () => {
  const first = toggleDraft(data, {}, '2026-11-08', 'morning');
  assert.equal(isDraftChanged(data, first, '2026-11-08', 'morning'), true);
  assert.equal(isDraftChanged(data, first, '2026-11-08', 'afternoon'), false);
  const unavailable = toggleDraft(data, first, '2026-11-08', 'afternoon');
  assert.equal(isDraftChanged(data, unavailable, '2026-11-08', 'afternoon'), true);
  assert.equal(isDraftChanged(data, toggleDraft(data, first, '2026-11-08', 'morning'), '2026-11-08', 'morning'), false);
  const both = toggleDraft(data, first, '2026-11-09', 'evening');
  assert.deepEqual(draftRows(both), [{ date: '2026-11-08', revision: 4, morning: 'AVAILABLE', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE' }, { date: '2026-11-09', revision: 0, morning: 'AVAILABLE', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE' }]);
  assert.equal(data.days[0].morning, 'UNKNOWN'); assert.equal(previewDraft(data, both).days[0].morning, 'AVAILABLE');
  assert.deepEqual(toggleDraft(data, first, '2026-11-08', 'morning'), {});
});

test('only absent own dates start available; stored states and snapshots remain untouched', () => {
  const original = structuredClone(data);
  assert.deepEqual(ownDraftDay(data, '2026-11-08'), { date: '2026-11-08', revision: 4, morning: 'UNAVAILABLE', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE' });
  assert.deepEqual(ownDraftDay(data, '2026-11-09'), { date: '2026-11-09', revision: 0, morning: 'AVAILABLE', afternoon: 'AVAILABLE', evening: 'AVAILABLE' });
  const drafts = toggleDraft(data, {}, '2026-11-09', 'morning');
  assert.equal(isDraftChanged(data, drafts, '2026-11-09', 'morning'), true);
  assert.equal(isDraftChanged(data, drafts, '2026-11-09', 'afternoon'), false);
  assert.deepEqual(toggleDraft(data, drafts, '2026-11-09', 'morning'), {});
  assert.deepEqual(data, original);
  assert.deepEqual(previewDraft(data, {}).days, original.days);
});

test('missing editor defaults never confirm a write and recovery detects stored row changes', () => {
  const date = '2026-11-09';
  const drafts = toggleDraft(data, {}, date, 'morning');
  assert.equal(reconcileDraft(data, drafts, data), 'unchanged');
  const allUnavailable = { [date]: { date, revision: 0, morning: 'UNAVAILABLE', afternoon: 'UNAVAILABLE', evening: 'UNAVAILABLE' } };
  assert.equal(reconcileDraft(data, allUnavailable, data), 'unchanged');
  const stored = { ...data, days: [...data.days, { ...drafts[date], membership_id: 7, revision: 1 }] };
  assert.equal(reconcileDraft(data, drafts, stored), 'confirmed');
  assert.equal(reconcileDraft(data, drafts, { ...stored, days: stored.days.map((row) => row.date === date ? { ...row, morning: 'AVAILABLE' } : row) }), 'conflict');
});
test('recovery only retains unchanged-baseline drafts, confirms matching writes and rejects reset or revision races', () => {
  const drafts = toggleDraft(data, {}, '2026-11-08', 'morning');
  assert.equal(reconcileDraft(data, drafts, data), 'unchanged');
  assert.equal(reconcileDraft(data, drafts, { ...previewDraft(data, drafts), days: previewDraft(data, drafts).days.map((row) => ({ ...row, revision: 5 })) }), 'confirmed');
  assert.equal(reconcileDraft(data, drafts, { ...data, generation: 3 }), 'conflict');
  assert.equal(reconcileDraft(data, drafts, { ...data, days: [{ ...data.days[0], revision: 5, evening: 'AVAILABLE' }] }), 'conflict');
});
