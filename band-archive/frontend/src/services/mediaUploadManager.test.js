import assert from 'node:assert/strict';
import test from 'node:test';
import { initiateMultipartSession } from './multipartSession.js';
import { normalizeMedia } from './mediaUploadManager.js';

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

test('wrapped v2 processing responses drive queued media through processing to completed', () => {
  let current = { id: 71, transcoding_status: 'queued', filename: 'practice.mp4' };
  current = normalizeMedia({ ...current, ...normalizeMedia({ processing: { status: 'processing', attempts: 1, started_at: '2026-10-03T00:00:00Z' } }) });
  assert.equal(current.transcoding_status, 'processing');
  current = normalizeMedia({ ...current, ...normalizeMedia({ processing: { status: 'completed', attempts: 1, audio_filename: 'practice.m4a', audio_url: 'https://media.example/practice.m4a', completed_at: '2026-10-03T00:01:00Z' } }) });
  assert.equal(current.id, 71);
  assert.equal(current.transcoding_status, 'completed');
  assert.equal(current.audio_filename, 'practice.m4a');
  assert.equal(current.audio_url, 'https://media.example/practice.m4a');
  assert.equal(current.processing_error, undefined);
});

test('wrapped v2 processing failure maps compact status and error to common poller fields', () => {
  const normalized = normalizeMedia({ processing: { status: 'failed', error: 'audio extraction failed', attempts: 2 } });
  assert.equal(normalized.transcoding_status, 'failed');
  assert.equal(normalized.processing_error, 'audio extraction failed');
  assert.equal(normalized.transcoding_error, 'audio extraction failed');
  assert.equal(normalized.error, 'audio extraction failed');
});

test('legacy raw and media-wrapped processing payloads retain their existing shape support', () => {
  const raw = normalizeMedia({ id: 7, status: 'processing', error: 'retrying' });
  const wrapped = normalizeMedia({ media: { id: 8, transcoding_status: 'completed', audio_url: 'https://media.example/a.m4a' } });
  assert.equal(raw.id, 7);
  assert.equal(raw.transcoding_status, 'processing');
  assert.equal(raw.processing_error, 'retrying');
  assert.equal(wrapped.id, 8);
  assert.equal(wrapped.transcoding_status, 'completed');
  assert.equal(wrapped.audio_url, 'https://media.example/a.m4a');
});
