import assert from 'node:assert/strict';
import test from 'node:test';
import { createUploadCompletionStore, uploadTargetsMatch } from './uploadCompletionStore.js';

test('completion store delivers each media completion once and makes late target mounts dirty', () => {
  const store = createUploadCompletionStore();
  const target = { songId: 4, rehearsalId: 9 };
  const received = [];
  const unsubscribe = store.subscribe(target, (event) => received.push(event));
  assert.equal(store.publish({ target, media: { id: 12 } }), true);
  assert.equal(store.publish({ target, media: { id: 12 } }), false);
  assert.equal(received.length, 1);
  unsubscribe();
  const late = [];
  store.subscribe(target, (event) => late.push(event));
  assert.equal(late[0].prior[0].media.id, 12);
  assert.equal(store.versionFor(target), 1);
});

test('completion targets isolate members and distinguish rehearsal destinations', () => {
  assert.equal(uploadTargetsMatch({ songId: 4, rehearsalId: 9 }, { songId: 4, rehearsalId: 9 }), true);
  assert.equal(uploadTargetsMatch({ songId: 4, rehearsalId: 9 }, { songId: 4, rehearsalId: 10 }), false);
  assert.equal(uploadTargetsMatch({ memberId: 2 }, { memberId: 2 }), true);
  assert.equal(uploadTargetsMatch({ memberId: 2 }, { songId: 2 }), false);
});
