import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizedJoinName, validJoinName } from './joinName.js';

test('join names are required, trimmed, bounded Unicode plain text', () => {
  for (const value of ['', '  ', null, undefined, 'a'.repeat(101), 'a\nb', '\tName', 'a\u200bb', '\ud800', '\udfff']) assert.equal(validJoinName(value), false);
  assert.equal(normalizedJoinName('  황기덕  '), '황기덕');
  assert.equal(validJoinName('  황기덕  '), true);
  assert.equal(validJoinName('😀'.repeat(100)), true);
  assert.equal(validJoinName('😀'.repeat(101)), false);
});
