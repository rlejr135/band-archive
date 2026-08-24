import assert from 'node:assert/strict';
import test from 'node:test';
import { acknowledgeNativeUpload, hydrateNativeUploadQueue, resetNativeUploadQueueForTest, subscribeNativeUploadQueue } from './nativeUploadQueue.js';

test('native queue hydrates once, fans out one listener, and acknowledges terminal result once', async () => {
  resetNativeUploadQueueForTest(); let list = 0; let nativeListener; const acked = [];
  const transport = { kind: 'native', listPending: async () => { list += 1; return { items: [{ id: 'done', state: 'completed', target: { song_id: 3 } }] }; }, addListener: async (_name, fn) => { nativeListener = fn; return () => {}; }, acknowledge: async ({ id }) => acked.push(id) };
  const received = []; const unsubscribe = subscribeNativeUploadQueue((value) => received.push(value));
  await hydrateNativeUploadQueue(transport); await hydrateNativeUploadQueue(transport);
  assert.equal(list, 1); assert.equal(received[0].target.song_id, 3); nativeListener({ id: 'live', state: 'uploading', progress: 10 }); assert.equal(received.at(-1).id, 'live');
  await acknowledgeNativeUpload(transport, 'done'); await acknowledgeNativeUpload(transport, 'done'); assert.deepEqual(acked, ['done']); unsubscribe(); resetNativeUploadQueueForTest();
});
