import test from 'node:test';
import assert from 'node:assert/strict';
import { getRehearsalUploadPlan } from './rehearsalUploadQueue.js';

test('a partial retry submits only the failed rehearsal file, never a completed file', () => {
  const items = [
    { file: { id: 'first' }, songId: 11, status: 'completed' },
    { file: { id: 'second' }, songId: 12, status: 'failed', error: 'network unavailable' },
  ];

  assert.deepEqual(
    getRehearsalUploadPlan({ items, rehearsalId: 7 }).map(({ index }) => index),
    [1],
  );
});

test('a saved media record with failed audio processing is not uploaded a second time', () => {
  const items = [{
    file: { id: 'saved-video' },
    songId: 11,
    mediaId: 99,
    status: 'processing_failed',
    error: 'audio extraction failed',
  }];

  assert.deepEqual(getRehearsalUploadPlan({ items, rehearsalId: 7 }), []);
});

test('a durably queued, processing, or completed native file is not enqueued again', () => {
  const item = { file: { id: 'native-file' }, songId: 11, status: 'failed' };

  for (const state of ['queued', 'processing', 'completed']) {
    assert.deepEqual(
      getRehearsalUploadPlan({
        items: [item],
        rehearsalId: 7,
        nativePending: [{ id: 'native-file', state, target: { song_id: 11, rehearsal_id: 7 } }],
      }),
      [],
    );
  }
});
