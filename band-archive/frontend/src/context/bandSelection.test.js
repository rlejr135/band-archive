import assert from 'node:assert/strict';
import test from 'node:test';
import { selectValidBand } from './bandSelection.js';

test('selected bands are retained only while active for the same user', () => {
  const bands = [{ id: 4, name: '들뜬' }, { id: 8, name: '연습실' }];
  assert.deepEqual(selectValidBand(8, bands), bands[1]);
  assert.equal(selectValidBand(99, bands), null);
  assert.equal(selectValidBand(8, []), null);
});
