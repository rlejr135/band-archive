import assert from 'node:assert/strict';
import test from 'node:test';
import { createSubmissionGuard } from './authSubmission.js';

test('submission guard prevents double submit and releases after a rejected auth action', async () => {
  const guard = createSubmissionGuard();
  assert.equal(guard.tryAcquire(), true);
  assert.equal(guard.tryAcquire(), false);
  try {
    await Promise.reject(new Error('network down'));
  } catch {
    // Every auth form maps this outcome to a generic response.
  } finally {
    guard.release();
  }
  assert.equal(guard.isActive(), false);
  assert.equal(guard.tryAcquire(), true);
});
