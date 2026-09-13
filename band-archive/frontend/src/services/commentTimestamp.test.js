import assert from 'node:assert/strict';
import test from 'node:test';
import { canAttachCommentTimestamp, formatCommentTimestamp, getCommentTimestampSeekPlan, seekCommentTimestamp, withCommentTimestamp } from './commentTimestamp.js';

test('comment timestamp formats minutes and hours accessibly', () => {
  assert.equal(formatCommentTimestamp(0), '0:00');
  assert.equal(formatCommentTimestamp(65), '1:05');
  assert.equal(formatCommentTimestamp(3665), '1:01:05');
  assert.equal(formatCommentTimestamp(-1), null);
});

test('comment timestamp payload distinguishes omitted, clear, and set values including zero', () => {
  assert.deepEqual(withCommentTimestamp({ content: 'one' }, undefined), { content: 'one' });
  assert.deepEqual(withCommentTimestamp({ content: 'one' }, null), { content: 'one', timestamp_seconds: null });
  assert.deepEqual(withCommentTimestamp({ content: 'one' }, 0), { content: 'one', timestamp_seconds: 0 });
  assert.throws(() => withCommentTimestamp({ content: 'one' }, -1), /올바르지/);
});

test('comment seek waits for metadata and clamps on the currently active media mode', () => {
  assert.deepEqual(getCommentTimestampSeekPlan(0, Number.NaN), { accepted: true, pending: true, time: 0 });
  assert.deepEqual(getCommentTimestampSeekPlan(50, 30), { accepted: true, pending: false, time: 30 });
  assert.deepEqual(getCommentTimestampSeekPlan(-1, 30), { accepted: false });
});

test('only top-level media comments can attach a timestamp and seek their own controller', () => {
  assert.equal(canAttachCommentTimestamp({ timestampEnabled: true, depth: 0 }), true);
  assert.equal(canAttachCommentTimestamp({ timestampEnabled: true, depth: 1 }), false);
  assert.equal(canAttachCommentTimestamp({ timestampEnabled: false, depth: 0 }), false);
  const firstCalls = [];
  const secondCalls = [];
  const players = [
    { seekTo: (time) => { firstCalls.push(time); return true; } },
    { seekTo: (time) => { secondCalls.push(time); return true; } },
  ];
  assert.equal(seekCommentTimestamp(players[0], 0), true);
  assert.deepEqual(firstCalls, [0]);
  assert.deepEqual(secondCalls, []);
});
