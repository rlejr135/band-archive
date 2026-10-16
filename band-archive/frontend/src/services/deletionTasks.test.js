import assert from 'node:assert/strict';
import test from 'node:test';
import { DeletionTaskError, deletionErrorMessage, pollDeletionTask } from './deletionTasks.js';

test('deletion task poller uses its supplied same-band path, clamps wait, and stops at completion', async () => {
  const calls = []; const delays = [];
  const api = { requestJson: async (path) => { calls.push(path); return calls.length === 1 ? { status: 'running', retry_after_seconds: 99 } : { status: 'completed' }; } };
  const result = await pollDeletionTask({ api, path: (id) => `/api/v2/bands/7/deletion-tasks/${encodeURIComponent(id)}`, taskId: 'task/one', retryAfterSeconds: 0, pause: async (delay) => delays.push(delay) });
  assert.equal(result.status, 'completed');
  assert.deepEqual(calls, ['/api/v2/bands/7/deletion-tasks/task%2Fone', '/api/v2/bands/7/deletion-tasks/task%2Fone']);
  assert.deepEqual(delays, [500, 3000]);
});

test('deletion task poller fails closed for blocked or malformed tasks and is bounded', async () => {
  const api = { requestJson: async () => ({ status: 'blocked', error_code: 'processing' }) };
  await assert.rejects(pollDeletionTask({ api, path: (id) => `/tasks/${id}`, taskId: 't1', pause: async () => {} }), (error) => error instanceof DeletionTaskError && error.code === 'processing');
  await assert.rejects(pollDeletionTask({ api, path: (id) => `/tasks/${id}`, taskId: '', pause: async () => {} }), /Deletion task failed/);
  let reads = 0;
  await assert.rejects(pollDeletionTask({ api: { requestJson: async () => { reads += 1; return { status: 'running' }; } }, path: (id) => `/tasks/${id}`, taskId: 't2', pause: async () => {} }), (error) => error instanceof DeletionTaskError && error.code === 'timeout');
  assert.equal(reads, 20);
  await assert.rejects(pollDeletionTask({ api: { requestJson: async () => ({ status: 'completed', resource_type: 'media', resource_id: 2 }) }, path: (id) => `/tasks/${id}`, taskId: 't3', expectedResourceType: 'gallery', expectedResourceId: 2, pause: async () => {} }), (error) => error instanceof DeletionTaskError && error.code === 'task_mismatch');
});

test('deletion errors give non-specific safe guidance for conflicts', () => {
  assert.match(deletionErrorMessage({ status: 409 }), /연결된 콘텐츠나 진행 중인 파일 처리/);
});
