import assert from 'node:assert/strict';
import test from 'node:test';
import { getMediaIcon, getMediaType } from './mediaPresentation.js';

test('media presentation prefers the declared type and falls back to a filename extension', () => {
  assert.equal(getMediaType({ file_type: 'video', filename: 'take.mp3' }), 'video');
  assert.equal(getMediaType({ filename: 'rehearsal.m4a' }), 'audio');
  assert.equal(getMediaType({ name: 'clip.MOV' }), 'video');
  assert.equal(getMediaType({ filename: 'photo.webp' }), 'image');
  assert.equal(getMediaType({ filename: 'chart.pdf' }), 'document');
});

test('media presentation returns a stable icon for every display type', () => {
  assert.equal(getMediaIcon({ file_type: 'video' }), '🎬');
  assert.equal(getMediaIcon({ filename: 'recording.wav' }), '🎵');
  assert.equal(getMediaIcon({ filename: 'unknown.bin' }), '📄');
});
