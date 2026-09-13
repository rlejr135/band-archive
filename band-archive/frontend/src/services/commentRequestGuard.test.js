import assert from 'node:assert/strict';
import test from 'node:test';
import { createCommentRequestGuard, getCommentTargetKey } from './commentRequestGuard.js';

test('comment target keys distinguish target switches and the latest request wins', () => {
  assert.notEqual(getCommentTargetKey('personal-logs', 'A'), getCommentTargetKey('personal-logs', 'B'));
  const guard = createCommentRequestGuard(getCommentTargetKey('personal-logs', 'A'));
  const initialRead = guard.begin();
  const submitRefresh = guard.begin();
  assert.equal(guard.isCurrent(initialRead), false);
  assert.equal(guard.isCurrent(submitRefresh), true);
});

test('switching target rejects a late A submit/read while B can load', () => {
  const targetA = createCommentRequestGuard(getCommentTargetKey('media', 42));
  const lateSubmitForA = targetA.begin();
  targetA.dispose();
  const targetB = createCommentRequestGuard(getCommentTargetKey('media', 43));
  const loadForB = targetB.begin();
  assert.equal(targetA.isActive(), false);
  assert.equal(targetA.isCurrent(lateSubmitForA), false);
  assert.equal(targetB.isCurrent(loadForB), true);
});
