import assert from 'node:assert/strict';
import test from 'node:test';
import { bandUploadTaskMessages, persistBandUploadTasks, restoreBandUploadTasks } from './bandUploadTaskStore.js';

const storage = () => {
  const values = new Map();
  return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value) };
};

test('persisted band task restores its immutable origin but never pretends a browser file survived reload', () => {
  const previousWindow = globalThis.window;
  globalThis.window = { localStorage: storage() };
  try {
    persistBandUploadTasks('account', [{ id: 't1', bandId: 11, bandName: '첫 밴드', status: 'uploading', file: { name: 'lost.mp4' } }]);
    const [task] = restoreBandUploadTasks('account');
    assert.equal(task.bandId, 11);
    assert.equal(task.bandName, '첫 밴드');
    assert.equal(task.status, 'failed');
    assert.equal(task.error, bandUploadTaskMessages.restartMessage);
    assert.equal(task.file, undefined);
  } finally { globalThis.window = previousWindow; }
});

test('unscoped legacy persisted tasks are explicitly failed instead of being retargeted to a current band', () => {
  const previousWindow = globalThis.window;
  globalThis.window = { localStorage: storage() };
  try {
    persistBandUploadTasks('account', [{ id: 'legacy', status: 'queued', target: { songId: 7 } }]);
    const [task] = restoreBandUploadTasks('account');
    assert.equal(task.status, 'failed');
    assert.equal(task.legacyUnscoped, true);
    assert.equal(task.error, bandUploadTaskMessages.legacyMessage);
    assert.equal(task.bandId, undefined);
  } finally { globalThis.window = previousWindow; }
});
