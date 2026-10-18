import assert from 'node:assert/strict';
import test from 'node:test';
import { bandIntroductionPath } from './bandIntroductionApi.js';

test('band introduction path URL-encodes the public slug', () => {
  assert.equal(bandIntroductionPath('quiet lantern/2026'), '/api/v2/bands/by-slug/quiet%20lantern%2F2026/introduction');
});
