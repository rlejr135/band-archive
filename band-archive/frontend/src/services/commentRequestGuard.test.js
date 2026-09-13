import assert from 'node:assert/strict';
import test from 'node:test';
import { createCommentRequestGuard, getCommentTargetKey } from './commentRequestGuard.js';

test('comment target keys distinguish target switches and the latest request wins', () => {
  assert.notEqual(getCommentTargetKey('personal-logs', 'A'), getCommentTargetKey('personal-logs', 'B'));
  const guard = createCommentRequestGuard(getCommentTargetKey('personal-logs', 'A'));
  const lifecycle = guard.activate();
  const initialRead = guard.begin(lifecycle);
  const submitRefresh = guard.begin(lifecycle);
  assert.equal(guard.isCurrent(initialRead), false);
  assert.equal(guard.isCurrent(submitRefresh), true);
});

test('switching target rejects a late A submit/read while B can load', () => {
  const targetA = createCommentRequestGuard(getCommentTargetKey('media', 42));
  const lifecycleA = targetA.activate();
  const lateSubmitForA = targetA.begin(lifecycleA);
  targetA.dispose(lifecycleA);
  const targetB = createCommentRequestGuard(getCommentTargetKey('media', 43));
  const lifecycleB = targetB.activate();
  const loadForB = targetB.begin(lifecycleB);
  assert.equal(targetA.isActive(lifecycleA), false);
  assert.equal(targetA.isCurrent(lateSubmitForA), false);
  assert.equal(targetB.isCurrent(loadForB), true);
});

test('StrictMode cleanup and second setup reactivate one guard without reviving first setup', () => {
  const guard = createCommentRequestGuard(getCommentTargetKey('media', 7));
  const firstSetup = guard.activate();
  const firstRead = guard.begin(firstSetup);
  guard.dispose(firstSetup);
  const secondSetup = guard.activate();
  const secondRead = guard.begin(secondSetup);
  assert.equal(guard.isActive(secondSetup), true);
  assert.equal(guard.isCurrent(firstRead), false);
  assert.equal(guard.isCurrent(secondRead), true);
});
