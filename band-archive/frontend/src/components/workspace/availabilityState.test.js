import assert from 'node:assert/strict';
import test from 'node:test';
import { adjacentMonth, availabilityFingerprint, batchDays, calendarWeeks, candidates, dateRange, inclusiveDates, monthDays, validDay } from './availabilityState.js';

const data = { generation: 2, my_membership_id: 1, members: [{ membership_id: 1 }, { membership_id: 2 }], days: [{ membership_id: 1, date: '2026-10-17', revision: 3, morning: 'UNAVAILABLE', afternoon: 'UNKNOWN', evening: 'AVAILABLE' }] };
test('availability date handling is timezone-independent, leap-safe and bounds 42 days', () => {
  assert.equal(validDay('2024-02-29'), true); assert.equal(validDay('2025-02-29'), false);
  assert.equal(monthDays('2024-02').length, 29); assert.equal(monthDays('2025-02').length, 28); assert.deepEqual(monthDays('2026-13'), []);
  assert.equal(adjacentMonth('2026-12', 1), '2027-01'); assert.equal(adjacentMonth('2026-01', -1), '2025-12');
  assert.equal(dateRange('2026-01-01', '2026-02-11').length, 42); assert.deepEqual(dateRange('2026-01-01', '2026-02-12'), []);
});
test('date selection and chosen periods preserve all unselected period values', () => {
  const dates = ['2026-10-17', '2026-10-18'];
  const chosen = inclusiveDates(dates, dates[1], dates[0]);
  const cells = chosen.flatMap((date) => ['afternoon', 'evening'].map((period) => `${date}:${period}`));
  assert.deepEqual(cells, ['2026-10-17:afternoon', '2026-10-17:evening', '2026-10-18:afternoon', '2026-10-18:evening']);
  assert.deepEqual(batchDays(data, cells, 'AVAILABLE'), [
    { date: dates[0], revision: 3, morning: 'UNAVAILABLE', afternoon: 'AVAILABLE', evening: 'AVAILABLE' },
    { date: dates[1], revision: 0, morning: 'UNKNOWN', afternoon: 'AVAILABLE', evening: 'AVAILABLE' },
  ]);
  assert.deepEqual(batchDays(data, ['2026-10-17:evening'], 'UNKNOWN')[0], { date: dates[0], revision: 3, morning: 'UNAVAILABLE', afternoon: 'UNKNOWN', evening: 'UNKNOWN' });
  assert.throws(() => batchDays(data, ['bad:evening'], 'AVAILABLE')); assert.throws(() => batchDays(data, [], 'AVAILABLE'));
});
test('month calendar uses Sunday-first seven columns with non-selectable boundary padding', () => {
  const weeks = calendarWeeks('2026-10');
  assert.equal(weeks.every((week) => week.length === 7), true);
  assert.deepEqual(weeks[0], [null, null, null, null, '2026-10-01', '2026-10-02', '2026-10-03']);
  assert.deepEqual(weeks.flat().filter(Boolean), monthDays('2026-10'));
  assert.equal(calendarWeeks('2024-02').flat().filter(Boolean).length, 29);
  assert.equal(calendarWeeks('0001-01').flat().filter(Boolean).length, 31);
  const dates = monthDays('2026-10');
  assert.deepEqual(inclusiveDates(dates, '2026-10-08', '2026-10-02'), dates.slice(1, 8));
  assert.deepEqual(inclusiveDates(dates, '2026-09-30', '2026-10-02'), []);
});
test('candidate ranking never treats missing responses as possible and ignores foreign members', () => {
  assert.deepEqual(candidates(data, ['2026-10-17'], []), []);
  assert.deepEqual(candidates(data, ['2026-10-17'], [999]), []);
  const ranked = candidates(data, ['2026-10-17'], [1, 2]);
  assert.equal(ranked[0].period, 'evening'); assert.equal(ranked[0].available, 1); assert.equal(ranked[0].unknown, 1); assert.equal(ranked[0].all, false);
  assert.equal(candidates(data, ['2026-10-17'], [1])[0].all, true);
  const empty = candidates({ ...data, days: [] }, ['2026-10-18', '2026-10-17'], [1, 2]);
  assert.equal(empty[0].date, '2026-10-17'); assert.equal(empty[0].period, 'morning'); assert.equal(empty.every((row) => !row.all && row.available === 0 && row.unknown === 2), true);
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
