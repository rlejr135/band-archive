import assert from 'node:assert/strict';
import test from 'node:test';
import { retainUploadTaskHistory } from './uploadTaskRetention.js';

test('failed browser history retains only the newest raw File while preserving older display rows', () => {
  const retained = retainUploadTaskHistory([
    { id: 'old', status: 'failed', file: { name: '1GiB-old.mp4' } },
    { id: 'active', status: 'uploading', file: { name: 'active.mp4' } },
    { id: 'new', status: 'failed', file: { name: '1GiB-new.mp4' } },
  ]);
  assert.equal(retained.find((item) => item.id === 'old').retryUnavailable, true);
  assert.equal(retained.find((item) => item.id === 'old').file, undefined);
  assert.equal(retained.find((item) => item.id === 'new').file.name, '1GiB-new.mp4');
  assert.equal(retained.find((item) => item.id === 'active').file.name, 'active.mp4');
});
