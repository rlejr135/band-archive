import test from 'node:test';
import assert from 'node:assert/strict';
import { createUploadJobRegistry, createUploadTaskId } from './uploadJobRegistry.js';

test('a job source is registered before its provider-owned batch start', async () => {
  const registry = createUploadJobRegistry();
  const source = { file: { name: 'practice.mp4' } };
  registry.registerSource('job-1', source);
  let observed;
  await registry.startOnce('job-1', () => { observed = registry.sourceFor('job-1'); });
  assert.equal(observed, source);
});

test('duplicate starts share one promise and execute the upload only once', async () => {
  const registry = createUploadJobRegistry();
  let starts = 0;
  const first = registry.startOnce('job-1', async () => { starts += 1; return 'media'; });
  const second = registry.startOnce('job-1', async () => { starts += 1; return 'duplicate'; });
  assert.equal(first, second);
  assert.equal(await first, 'media');
  assert.equal(starts, 1);
});

test('terminal history can release a retained browser File without cancelling an active job', async () => {
  const registry = createUploadJobRegistry();
  registry.registerSource('job-1', { file: { name: 'large.mp4' } });
  const active = registry.startOnce('job-1', () => new Promise(() => {}));
  registry.releaseSource('job-1');
  assert.equal(registry.sourceFor('job-1'), undefined);
  assert.equal(registry.hasStarted('job-1'), true);
  assert.ok(active instanceof Promise);
});

test('cancelling invalidates pending callbacks and prevents a queued batch item from starting', async () => {
  const registry = createUploadJobRegistry();
  const first = registry.startOnce('job-1', async (attempt) => {
    registry.cancel('job-1');
    return registry.isCurrent('job-1', attempt);
  });
  assert.equal(await first, false);
  await assert.rejects(registry.startOnce('job-1', () => 'must not run'), { name: 'AbortError' });
});

test('a later attempt has a distinct callback generation', async () => {
  const registry = createUploadJobRegistry();
  let originalAttempt;
  await registry.startOnce('job-1', async (attempt) => { originalAttempt = attempt; });
  let retryAttempt;
  await registry.startOnce('job-1', async (attempt) => { retryAttempt = attempt; });
  assert.notEqual(retryAttempt, originalAttempt);
  assert.equal(registry.isCurrent('job-1', originalAttempt), false);
  assert.equal(registry.isCurrent('job-1', retryAttempt), true);
});

test('native task identity includes its target and source ownership rejects concurrent target changes', () => {
  const registry = createUploadJobRegistry();
  const first = createUploadTaskId({ file: { id: 'picked-file' }, target: { songId: 4, rehearsalId: 9 } });
  const second = createUploadTaskId({ file: { id: 'picked-file' }, target: { songId: 4, rehearsalId: 10 } });
  assert.notEqual(first, second);
  assert.equal(registry.claimNativeSource('picked-file', first), first);
  assert.equal(registry.claimNativeSource('picked-file', first), first);
  assert.equal(registry.claimNativeSource('picked-file', second), first);
  registry.releaseNativeSource('picked-file', first);
  assert.equal(registry.claimNativeSource('picked-file', second), second);
});
