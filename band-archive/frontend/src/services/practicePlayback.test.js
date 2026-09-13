import assert from 'node:assert/strict';
import test from 'node:test';
import { clampPracticeMarkers, clampPracticeTime, formatPracticeTime, getPracticeLoopDecision, normalizePracticeRate, validatePracticeLoop } from './practicePlayback.js';

test('practice time formatting and seeks clamp to a known duration', () => {
  assert.equal(formatPracticeTime(65.9), '1:05');
  assert.equal(clampPracticeTime(-2, 30), 0);
  assert.equal(clampPracticeTime(40, 30), 30);
  assert.equal(clampPracticeTime(4, Number.NaN), 0);
});

test('A-B loop validation rejects unknown, inverted, and too-short ranges', () => {
  assert.equal(validatePracticeLoop({ start: 1, end: 3, duration: Number.NaN }).valid, false);
  assert.match(validatePracticeLoop({ start: 4, end: 3, duration: 30 }).reason, /뒤여야/);
  assert.match(validatePracticeLoop({ start: 4, end: 4.1, duration: 30 }).reason, /이상/);
  assert.deepEqual(validatePracticeLoop({ start: 4, end: 9, duration: 30 }), { valid: true, start: 4, end: 9 });
});

test('loop decision seeks at B and duration changes clamp markers', () => {
  assert.deepEqual(getPracticeLoopDecision({ time: 9, start: 4, end: 9, duration: 30, enabled: true }).shouldLoop, true);
  assert.equal(getPracticeLoopDecision({ time: 8.9, start: 4, end: 9, duration: 30, enabled: true }).shouldLoop, false);
  assert.deepEqual(clampPracticeMarkers({ start: 20, end: 30 }, 22), { start: 20, end: 22 });
  assert.equal(normalizePracticeRate(1.25), 1.25);
  assert.equal(normalizePracticeRate(2), 1);
});
