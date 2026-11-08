import assert from 'node:assert/strict';
import test from 'node:test';
import { draftRows, previewDraft, reconcileDraft, toggleDraft } from './availabilityDraft.js';
const data = { band_id: 1, generation: 2, my_membership_id: 7, members: [{ membership_id: 7 }], days: [{ membership_id: 7, date: '2026-11-08', revision: 4, morning: 'UNKNOWN', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE' }] };
test('draft toggles preserve original revisions, remove reverted days and never change baseline', () => {
  const first = toggleDraft(data, {}, '2026-11-08', 'morning');
  const both = toggleDraft(data, first, '2026-11-09', 'evening');
  assert.deepEqual(draftRows(both), [{ date: '2026-11-08', revision: 4, morning: 'AVAILABLE', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE' }, { date: '2026-11-09', revision: 0, morning: 'UNAVAILABLE', afternoon: 'UNAVAILABLE', evening: 'AVAILABLE' }]);
  assert.equal(data.days[0].morning, 'UNKNOWN'); assert.equal(previewDraft(data, both).days[0].morning, 'AVAILABLE');
  assert.deepEqual(toggleDraft(data, first, '2026-11-08', 'morning'), {});
});
test('recovery only retains unchanged-baseline drafts, confirms matching writes and rejects reset or revision races', () => {
  const drafts = toggleDraft(data, {}, '2026-11-08', 'morning');
  assert.equal(reconcileDraft(data, drafts, data), 'unchanged');
  assert.equal(reconcileDraft(data, drafts, { ...previewDraft(data, drafts), days: previewDraft(data, drafts).days.map((row) => ({ ...row, revision: 5 })) }), 'confirmed');
  assert.equal(reconcileDraft(data, drafts, { ...data, generation: 3 }), 'conflict');
  assert.equal(reconcileDraft(data, drafts, { ...data, days: [{ ...data.days[0], revision: 5, evening: 'AVAILABLE' }] }), 'conflict');
});
