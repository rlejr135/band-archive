import assert from 'node:assert/strict';
import test from 'node:test';
import { activeBandsOnly, selectValidBand } from './bandSelection.js';

test('selected bands are retained only while active for the same user', () => {
  const bands = [{ id: 4, name: '들뜬', membership: { status: 'active' } }, { id: 8, name: '연습실', membership: { status: 'active' } }];
  assert.deepEqual(selectValidBand(8, bands), bands[1]);
  assert.equal(selectValidBand(99, bands), null);
  assert.equal(selectValidBand(8, []), null);
});

test('pending and public-only bands cannot become account-scoped selections', () => {
  // Exact active `/api/v2/bands` shape: band_dict embeds membership id/role/
  // user but does not serialize lifecycle status in this endpoint.
  const accountA = activeBandsOnly([{ id: 1, membership: { id: 11, role: 'member', user: { id: 9 } } }, { id: 2, membership: { status: 'pending' } }, { id: 4, name: 'public only' }]);
  const accountB = activeBandsOnly([{ id: 3, membership: { status: 'active' } }]);
  assert.deepEqual(accountA.map((band) => band.id), [1]);
  assert.equal(selectValidBand(2, accountA), null);
  // Account replacement has a new active list, so A's stored id is rejected.
  assert.equal(selectValidBand(1, accountB), null);
  assert.equal(selectValidBand(3, accountB).id, 3);
});

test('explicit future inactive lifecycle shapes are rejected while missing status remains active-list compatible', () => {
  const bands = activeBandsOnly([
    { id: 1, membership: { id: 10, role: 'owner', user: { id: 1 } } },
    { id: 2, membership: { status: 'rejected' } },
    { id: 3, membership: { status: 'left' } },
    { id: 4, membership: null },
  ]);
  assert.deepEqual(bands.map((band) => band.id), [1]);
});
