import assert from 'node:assert/strict';
import test from 'node:test';
import { buildCommentThreads, mergeCommentPage, voteTarget } from './mediaEngagement.js';

test('comment pages dedupe deterministically and reconstruct bounded safe threads', () => {
  const comments = mergeCommentPage(
    [{ id: 1, content: 'root', created_at: '2026-01-01T00:00:00Z', parent_id: null }],
    [
      { id: 2, content: 'reply', created_at: '2026-01-02T00:00:00Z', parent_id: 1 },
      { id: 2, content: 'duplicate', created_at: '2026-01-03T00:00:00Z', parent_id: 1 },
      { id: 3, content: 'orphan', created_at: '2026-01-03T00:00:00Z', parent_id: 99 },
      { id: 4, content: 'cycle-a', created_at: '2026-01-04T00:00:00Z', parent_id: 5 },
      { id: 5, content: 'cycle-b', created_at: '2026-01-05T00:00:00Z', parent_id: 4 },
    ],
  );
  assert.deepEqual(comments.map((item) => item.id), [1, 2, 3, 4, 5]);
  const trees = buildCommentThreads(comments, 3);
  assert.deepEqual(trees.map((item) => item.id), [1, 3, 4, 5]);
  assert.deepEqual(trees[0].replies.map((item) => item.id), [2]);
});

test('vote controls use exact toggle/removal semantics', () => {
  assert.equal(voteTarget(0, 1), 1);
  assert.equal(voteTarget(1, 1), 0);
  assert.equal(voteTarget(1, -1), -1);
});
