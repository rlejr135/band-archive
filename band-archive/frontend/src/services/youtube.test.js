import assert from 'node:assert/strict';
import test from 'node:test';
import { getYoutubeId, isValidYoutubeUrl } from './youtube.js';

test('YouTube utility accepts the existing form URLs and extracts their IDs', () => {
  for (const url of [
    'https://www.youtube.com/watch?v=abc-123',
    'https://youtu.be/abc-123',
    'https://youtube.com/shorts/abc-123',
  ]) {
    assert.equal(isValidYoutubeUrl(url), true);
    assert.equal(getYoutubeId(url), 'abc-123');
  }
});

test('YouTube utility keeps optional links valid and rejects other hosts', () => {
  assert.equal(isValidYoutubeUrl(''), true);
  assert.equal(isValidYoutubeUrl('https://example.com/watch?v=abc-123'), false);
  assert.equal(getYoutubeId('https://example.com/watch?v=abc-123'), 'abc-123');
  assert.equal(getYoutubeId(null), null);
});
