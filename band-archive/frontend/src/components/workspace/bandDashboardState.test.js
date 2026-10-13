import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isoMonthFromDate,
  mergeDashboardImages,
  mergeDashboardPage,
  parseIsoDate,
  rehearsalsForDate,
  strictPositiveId,
} from './bandDashboardState.js';

test('dashboard link ids accept only strict positive safe integers', () => {
  assert.equal(strictPositiveId('42'), 42);
  for (const value of ['0', '-1', '01', '1.0', '1e2', ' 1', '9007199254740992', '']) {
    assert.equal(strictPositiveId(value), null);
  }
  assert.equal(strictPositiveId(42), null);
});

test('dashboard dates parse local calendar days and form a stable month key', () => {
  assert.equal(parseIsoDate('2026-02-28').getDate(), 28);
  assert.equal(parseIsoDate('2026-02-30'), null);
  assert.equal(parseIsoDate('2026-2-03'), null);
  assert.equal(isoMonthFromDate(new Date(2026, 9, 1)), '2026-10');
});

test('selected rehearsal days include single dates and ranges but reject malformed dates', () => {
  const items = [
    { id: 1, date: '2026-10-12' },
    { id: 2, start_date: '2026-10-10', end_date: '2026-10-14' },
    { id: 3, date: '2026-02-30' },
    { id: 4, start_date: '2026-10-15', end_date: '2026-10-12' },
    { id: 5, date: '2026-10-12', start_date: '2026-10-15', end_date: null },
    { id: 6, date: '2026-10-13', start_date: '2026-10-10', end_date: null },
  ];
  assert.deepEqual(rehearsalsForDate(items, '2026-10-12').map(({ id }) => id), [1, 2, 5]);
  assert.deepEqual(rehearsalsForDate(items, '2026-10-13').map(({ id }) => id), [2, 6]);
});

test('dashboard pages replace duplicate rehearsal DTOs and append new rows', () => {
  const previous = { month: '2026-10', stats: { total_songs: 1 }, month_rehearsals: { items: [{ id: 1, title: 'old' }], pagination: { has_more: true, next_offset: 25 } } };
  const incoming = { month: '2026-10', stats: { total_songs: 2 }, month_rehearsals: { items: [{ id: 1, title: 'fresh' }, { id: 2, title: 'next' }], pagination: { has_more: true, next_offset: 50 } } };
  const merged = mergeDashboardPage(previous, incoming, { append: true });
  assert.equal(merged.stats.total_songs, 2);
  assert.deepEqual(merged.month_rehearsals.items.map(({ title }) => title), ['fresh', 'next']);
  assert.equal(merged.month_rehearsals.pagination.next_offset, 50);
});

test('image refresh replaces only image slots and preserves event pagination state', () => {
  const previous = { month: '2026-10', stats: { total_rehearsals: 5 }, featured_gallery: { id: 3, url: 'expired-featured' }, recent_gallery: [{ id: 4, url: 'expired' }], month_rehearsals: { items: [{ id: 1 }, { id: 2 }], pagination: { has_more: true, next_offset: 50 } } };
  const incoming = { month: '2026-10', stats: { total_rehearsals: 2 }, featured_gallery: { id: 3, url: 'fresh-featured' }, recent_gallery: [{ id: 4, url: 'fresh' }], month_rehearsals: { items: [{ id: 1 }], pagination: { has_more: false, next_offset: null } } };
  const merged = mergeDashboardImages(previous, incoming);
  assert.equal(merged.featured_gallery.url, 'fresh-featured');
  assert.equal(merged.recent_gallery[0].url, 'fresh');
  assert.deepEqual(merged.month_rehearsals.items.map(({ id }) => id), [1, 2]);
  assert.equal(merged.month_rehearsals.pagination.next_offset, 50);
  assert.equal(merged.month_rehearsals.pagination.has_more, true);
  assert.equal(merged.stats.total_rehearsals, 5);
});
