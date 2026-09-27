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
  const accountA = activeBandsOnly([{ id: 1, membership: { status: 'active' } }, { id: 2, membership: { status: 'pending' } }, { id: 4, name: 'public only' }]);
  const accountB = activeBandsOnly([{ id: 3, membership: { status: 'active' } }]);
  assert.deepEqual(accountA.map((band) => band.id), [1]);
  assert.equal(selectValidBand(2, accountA), null);
  // Account replacement has a new active list, so A's stored id is rejected.
  assert.equal(selectValidBand(1, accountB), null);
  assert.equal(selectValidBand(3, accountB).id, 3);
});
