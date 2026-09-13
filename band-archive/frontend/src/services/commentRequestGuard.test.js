import assert from 'node:assert/strict';
import test from 'node:test';
import { createCommentRequestGuard, getCommentTargetKey } from './commentRequestGuard.js';

test('comment target keys distinguish target switches and the latest read wins', () => {
  assert.notEqual(getCommentTargetKey('personal-logs', 'A'), getCommentTargetKey('personal-logs', 'B'));
  const guard = createCommentRequestGuard(getCommentTargetKey('personal-logs', 'A'));
  const lifecycle = guard.activate();
  const initialRead = guard.beginRead(lifecycle);
  const refreshRead = guard.beginRead(lifecycle);
  assert.equal(guard.isCurrentRead(initialRead), false);
  assert.equal(guard.isCurrentRead(refreshRead), true);
});

test('switching target rejects a late A submit/read while B can load', () => {
  const targetA = createCommentRequestGuard(getCommentTargetKey('media', 42));
  const lifecycleA = targetA.activate();
  const lateSubmitForA = targetA.beginMutation(lifecycleA);
  targetA.dispose(lifecycleA);
  const targetB = createCommentRequestGuard(getCommentTargetKey('media', 43));
  const lifecycleB = targetB.activate();
  const loadForB = targetB.beginRead(lifecycleB);
  assert.equal(targetA.isActive(lifecycleA), false);
  assert.equal(targetA.isCurrentMutation(lateSubmitForA), false);
  assert.equal(targetB.isCurrentRead(loadForB), true);
});

test('StrictMode cleanup and second setup reactivate one guard without reviving first setup', () => {
  const guard = createCommentRequestGuard(getCommentTargetKey('media', 7));
  const firstSetup = guard.activate();
  const firstRead = guard.beginRead(firstSetup);
  guard.dispose(firstSetup);
  const secondSetup = guard.activate();
  const secondRead = guard.beginRead(secondSetup);
  assert.equal(guard.isActive(secondSetup), true);
  assert.equal(guard.isCurrentRead(firstRead), false);
  assert.equal(guard.isCurrentRead(secondRead), true);
});

test('a failed mutation does not suppress an in-flight initial read or its loading settle', () => {
  const guard = createCommentRequestGuard(getCommentTargetKey('media', 8));
  const lifecycle = guard.activate();
  const initialRead = guard.beginRead(lifecycle);
  const failedSubmit = guard.beginMutation(lifecycle);
  assert.equal(guard.isCurrentMutation(failedSubmit), true);
  // Simulate the POST rejection: the mutation has no refresh, but the read
  // remains eligible to render and clear its independent loading state.
  assert.equal(guard.isCurrentRead(initialRead), true);
});

test('a successful submit starts a newer read while an older read becomes stale', () => {
  const guard = createCommentRequestGuard(getCommentTargetKey('media', 9));
  const lifecycle = guard.activate();
  const initialRead = guard.beginRead(lifecycle);
  const submit = guard.beginMutation(lifecycle);
  assert.equal(guard.isCurrentMutation(submit), true);
  const refresh = guard.beginRead(lifecycle);
  assert.equal(guard.isCurrentRead(initialRead), false);
  assert.equal(guard.isCurrentRead(refresh), true);
});

test('stale cleanup cannot deactivate a newer StrictMode lifecycle', () => {
  const guard = createCommentRequestGuard(getCommentTargetKey('media', 10));
  const firstLifecycle = guard.activate();
  const secondLifecycle = guard.activate();
  guard.dispose(firstLifecycle);
  const currentRead = guard.beginRead(secondLifecycle);
  assert.equal(guard.isActive(secondLifecycle), true);
  assert.equal(guard.isCurrentRead(currentRead), true);
});
