import assert from 'node:assert/strict';
import test from 'node:test';
import { initiateMultipartSession } from './multipartSession.js';

test('cancelling while multipart initiate is in flight aborts the new server session', async () => {
  let cancelled = false;
  let retained;
  let aborted;
  await assert.rejects(initiateMultipartSession({
    initiate: async () => { cancelled = true; return { sessionId: 'session-1', capabilityToken: 'token-1' }; },
    setSession: (session) => { retained = session; },
    isCancelled: () => cancelled,
    abort: async (session) => { aborted = session; },
  }), { name: 'AbortError' });
  assert.deepEqual(retained, { sessionId: 'session-1', capabilityToken: 'token-1' });
  assert.deepEqual(aborted, retained);
});

test('multipart cleanup failure preserves the original cancellation result', async () => {
  await assert.rejects(initiateMultipartSession({
    initiate: async () => ({ sessionId: 'session-2', capabilityToken: 'token-2' }),
    isCancelled: () => true,
    abort: async () => { throw new Error('cleanup unavailable'); },
  }), { name: 'AbortError' });
});
