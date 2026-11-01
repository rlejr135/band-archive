import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeMedia } from './mediaUploadManager.js';
import { nativeUploadResult } from './nativeUploadQueue.js';

for (const [label, input] of [
  ['undefined', undefined], ['null', null], ['false', false],
  ['zero', 0], ['empty string', ''], ['truthy non-object', 'pending'],
]) {
  test(`upload response preserves ${label} without manufacturing a media record`, () => {
    assert.equal(normalizeMedia(input), input);
  });
}

test('upload envelopes prefer media, then personal log, then processing over the outer response', () => {
  const media = { id: 11, filename: 'song.mp4' };
  const personalLog = { id: 12, title: 'Bass practice' };
  const processing = { status: 'queued' };
  const response = { id: 99, media, personal_log: personalLog, processing };
  assert.equal(normalizeMedia(response), media);
  assert.equal(normalizeMedia({ ...response, media: null }), personalLog);
  assert.equal(normalizeMedia({ ...response, media: false, personal_log: '' }).transcoding_status, 'queued');
  assert.equal(response.media, media);
  assert.equal(response.personal_log, personalLog);
});

test('truthy envelope values win without validating or merging sibling payloads', () => {
  assert.equal(normalizeMedia({ media: 'opaque-result', personal_log: { id: 12 } }), 'opaque-result');
  const emptyMedia = {};
  assert.equal(normalizeMedia({ media: emptyMedia, processing: { status: 'processing' }, id: 99 }), emptyMedia);
  const raw = { id: 13, media: null, personal_log: 0, processing: false };
  assert.equal(normalizeMedia(raw), raw);
});

test('processing envelope supplies its fields without copying outer upload metadata', () => {
  const result = normalizeMedia({
    id: 99,
    filename: 'outer.mp4',
    processing: { status: 'processing', attempts: 2, started_at: '2026-11-01T00:00:00Z' },
  });
  assert.equal(result.transcoding_status, 'processing');
  assert.equal(result.attempts, 2);
  assert.equal(result.started_at, '2026-11-01T00:00:00Z');
  assert.equal(Object.hasOwn(result, 'id'), false);
  assert.equal(Object.hasOwn(result, 'filename'), false);
});

test('a payload without a truthy status keeps its identity and does not add aliases', () => {
  const media = Object.freeze({ id: 14, status: '', transcoding_status: null, error: 'original error' });
  assert.equal(normalizeMedia({ personal_log: media }), media);
  assert.equal(Object.hasOwn(media, 'processing_error'), false);
  assert.equal(Object.hasOwn(media, 'transcoding_error'), false);
});

test('truthy compact status takes priority while empty compact status falls back', () => {
  const preferred = normalizeMedia({ status: 'processing', transcoding_status: 'queued' });
  assert.equal(preferred.status, 'processing');
  assert.equal(preferred.transcoding_status, 'processing');
  const fallback = normalizeMedia({ status: '', transcoding_status: 'failed' });
  assert.equal(fallback.status, '');
  assert.equal(fallback.transcoding_status, 'failed');
  assert.equal(normalizeMedia({ status: 'provider-specific-state' }).transcoding_status, 'provider-specific-state');
});

test('distinct error fields preserve their values while processing error uses the first truthy alias', () => {
  const result = normalizeMedia({
    id: 15, status: 'failed', processing_error: 'processing detail',
    transcoding_error: 'transcoding detail', error: 'general detail',
  });
  assert.equal(result.processing_error, 'processing detail');
  assert.equal(result.transcoding_error, 'transcoding detail');
  assert.equal(result.error, 'general detail');
});

for (const [label, errors, expected] of [
  ['processing error', { processing_error: 'processing detail' }, 'processing detail'],
  ['transcoding error', { processing_error: '', transcoding_error: 'transcoding detail' }, 'transcoding detail'],
  ['general error', { processing_error: null, transcoding_error: '', error: 'general detail' }, 'general detail'],
  ['no error', {}, undefined],
  ['empty general error', { error: '' }, ''],
]) {
  test(`upload response populates error aliases from ${label}`, () => {
    const result = normalizeMedia({ status: 'failed', ...errors });
    assert.equal(result.processing_error, expected);
    assert.equal(result.transcoding_error, expected);
    assert.equal(result.error, expected);
  });
}

test('normalization preserves unknown and nested fields without mutating the selected payload', () => {
  const playback = Object.freeze({ audio_url: 'https://media.invalid/audio.m4a' });
  const original = Object.freeze({
    id: 16, status: 'completed', audio_filename: 'practice.m4a',
    audio_url: 'https://media.invalid/practice.m4a', playback,
    custom_marker: 'retained', error: null,
  });
  const envelope = Object.freeze({ personal_log: original, request_marker: 'outer only' });
  const normalized = normalizeMedia(envelope);
  assert.notEqual(normalized, original);
  assert.equal(normalized.id, 16);
  assert.equal(normalized.audio_filename, original.audio_filename);
  assert.equal(normalized.audio_url, original.audio_url);
  assert.equal(normalized.playback, playback);
  assert.equal(normalized.custom_marker, 'retained');
  assert.equal(Object.hasOwn(normalized, 'request_marker'), false);
  assert.equal(original.error, null);
  assert.equal(Object.hasOwn(original, 'processing_error'), false);
  assert.equal(Object.hasOwn(original, 'transcoding_status'), false);
  assert.deepEqual(normalizeMedia(normalized), normalized);
});

for (const kind of ['media', 'personal_log']) {
  test(`minimal native ${kind} result remains usable by the processing poller`, () => {
    const compact = { id: 17, status: 'processing', transcoding_status: 'processing' };
    const nativeResult = nativeUploadResult({ result: JSON.stringify({ [kind]: compact }) });
    const normalized = normalizeMedia(nativeResult);
    assert.equal(normalized.id, 17);
    assert.equal(normalized.status, 'processing');
    assert.equal(normalized.transcoding_status, 'processing');
    assert.equal(Object.hasOwn(normalized, 'filename'), false);
    assert.equal(Object.hasOwn(normalized, 'audio_url'), false);
    assert.deepEqual(normalizeMedia(normalized), normalized);
  });
}
