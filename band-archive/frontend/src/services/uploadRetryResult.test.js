import assert from 'node:assert/strict';
import test from 'node:test';
import { getAudioRetryResult } from './uploadRetryResult.js';

test('cancelled audio retries never become completed without a media result', () => {
  assert.deepEqual(getAudioRetryResult({ cancelled: true, media: { id: 4 } }), { outcome: 'cancelled', media: null });
  assert.equal(getAudioRetryResult({ media: null }).outcome, 'processing_failed');
  assert.equal(getAudioRetryResult({ media: { id: 4, transcoding_status: 'failed' } }).outcome, 'processing_failed');
});
