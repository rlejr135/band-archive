import assert from 'node:assert/strict';
import test from 'node:test';
import { adjacentMonth, availabilityFingerprint, batchDays, calendarWeeks, candidates, candidateDates, dateRange, aggregateSlot, slotChoices, toggleDay, monthDays, validDay } from './availabilityState.js';

const data = { generation: 2, my_membership_id: 1, members: [{ membership_id: 1 }, { membership_id: 2 }], days: [{ membership_id: 1, date: '2026-10-17', revision: 3, morning: 'UNAVAILABLE', afternoon: 'UNKNOWN', evening: 'AVAILABLE' }] };
test('availability date handling is timezone-independent, leap-safe and bounds 42 days', () => {
  assert.equal(validDay('2024-02-29'), true); assert.equal(validDay('2025-02-29'), false);
  assert.equal(monthDays('2024-02').length, 29); assert.equal(monthDays('2025-02').length, 28); assert.deepEqual(monthDays('2026-13'), []);
  assert.equal(adjacentMonth('2026-12', 1), '2027-01'); assert.equal(adjacentMonth('2026-01', -1), '2025-12');
  assert.equal(dateRange('2026-01-01', '2026-02-11').length, 42); assert.deepEqual(dateRange('2026-01-01', '2026-02-12'), []);
});
test('single slot toggle preserves revision and other periods, treating missing or legacy as X', () => {
  assert.deepEqual(toggleDay(data, '2026-10-17', 'morning'), [{ date: '2026-10-17', revision: 3, morning: 'AVAILABLE', afternoon: 'UNAVAILABLE', evening: 'AVAILABLE' }]);
  assert.deepEqual(toggleDay(data, '2026-10-17', 'evening')[0], { date: '2026-10-17', revision: 3, morning: 'UNAVAILABLE', afternoon: 'UNAVAILABLE', evening: 'UNAVAILABLE' });
  assert.deepEqual(toggleDay(data, '2026-10-18', 'afternoon')[0], { date: '2026-10-18', revision: 0, morning: 'UNAVAILABLE', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE' });
  assert.throws(() => toggleDay({ ...data, days: [{ ...data.days[0], revision: -1 }] }, '2026-10-17', 'morning'));
  assert.throws(() => toggleDay({ ...data, days: [{ ...data.days[0], afternoon: undefined }] }, '2026-10-17', 'morning'));
  assert.throws(() => batchDays(data, ['bad:evening'], 'AVAILABLE'));
});
test('view aggregation includes all active members and excludes foreign rows', () => {
  assert.equal(aggregateSlot(slotChoices(data, '2026-10-17', 'evening')), '△');
  assert.equal(aggregateSlot(slotChoices(data, '2026-10-17', 'afternoon')), '-');
  const everyone = { ...data, days: [...data.days, { membership_id: 2, date: '2026-10-17', evening: 'AVAILABLE' }, { membership_id: 999, date: '2026-10-17', evening: 'UNAVAILABLE' }] };
  assert.equal(aggregateSlot(slotChoices(everyone, '2026-10-17', 'evening')), 'O');
  assert.equal(slotChoices(everyone, '2026-10-17', 'evening').length, 2);
  assert.equal(aggregateSlot([]), '—');
});
test('month calendar uses Sunday-first seven columns with non-selectable boundary padding', () => {
  const weeks = calendarWeeks('2026-10');
  assert.equal(weeks.every((week) => week.length === 7), true);
  assert.deepEqual(weeks[0], [null, null, null, null, '2026-10-01', '2026-10-02', '2026-10-03']);
  assert.deepEqual(weeks.flat().filter(Boolean), monthDays('2026-10'));
  assert.equal(calendarWeeks('2024-02').flat().filter(Boolean).length, 29);
  assert.equal(calendarWeeks('0001-01').flat().filter(Boolean).length, 31);
});
test('candidate ranking never treats missing responses as possible and ignores foreign members', () => {
  assert.deepEqual(candidates(data, ['2026-10-17'], []), []);
  assert.deepEqual(candidates(data, ['2026-10-17'], [999]), []);
  const ranked = candidates(data, ['2026-10-17'], [1, 2]);
  assert.equal(ranked[0].period, 'evening'); assert.equal(ranked[0].available, 1); assert.equal(ranked[0].unavailable, 1); assert.equal(ranked[0].all, false);
  assert.equal(candidates(data, ['2026-10-17'], [1])[0].all, true);
  const empty = candidates({ ...data, days: [] }, ['2026-10-18', '2026-10-17'], [1, 2]);
  assert.equal(empty[0].date, '2026-10-17'); assert.equal(empty[0].period, 'morning'); assert.equal(empty.every((row) => !row.all && row.available === 0 && row.unavailable === 2), true);
  assert.equal(candidates(data, ['2026-10-17', '2026-10-18'], [1, 2], '2026-10-18').every((row) => row.date === '2026-10-18'), true);
  assert.deepEqual(candidates(data, ['2026-10-17'], [1, 2], '2026-10-18'), []);
});
test('candidate change token contains no schedule states or notes and changes after reset or new revision', () => {
  const token = availabilityFingerprint(data, '2026-10-17');
  assert.doesNotMatch(token, /AVAILABLE|UNKNOWN|UNAVAILABLE|body|morning/);
  assert.notEqual(token, availabilityFingerprint({ ...data, generation: 3 }, '2026-10-17'));
  assert.notEqual(token, availabilityFingerprint({ ...data, days: [{ ...data.days[0], revision: 4 }] }, '2026-10-17'));
  assert.notEqual(token, availabilityFingerprint({ ...data, members: data.members.slice(0, 1) }, '2026-10-17'));
});

test('candidate dates deduplicate the complete ranking before taking six dates and retain every period', () => {
  const ranked = [{ date: '2026-11-08', period: 'evening' }, { date: '2026-11-08', period: 'morning' }, { date: '2026-11-08', period: 'afternoon' }, ...Array.from({ length: 6 }, (_, index) => ({ date: `2026-11-${String(index + 9).padStart(2, '0')}`, period: 'morning' }))];
  const grouped = candidateDates(ranked);
  assert.equal(grouped.length, 6); assert.equal(grouped[0].best.period, 'evening'); assert.equal(grouped[0].periods.length, 3);
  assert.deepEqual(grouped.map((entry) => entry.date), ['2026-11-08', '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13']);
});
