import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeEmail, safeReturnPath } from './authRedirect.js';

test('normalizes email input without altering the password', () => {
  assert.equal(normalizeEmail('  Musician@Example.COM  '), 'musician@example.com');
  assert.equal(normalizeEmail(null), '');
});

test('accepts only same-origin internal return paths', () => {
  assert.equal(safeReturnPath('/bands?tab=mine'), '/bands?tab=mine');
  assert.equal(safeReturnPath('/songs/42#media'), '/songs/42#media');
  assert.equal(safeReturnPath('https://attacker.example/bands'), null);
  assert.equal(safeReturnPath('//attacker.example/bands'), null);
  assert.equal(safeReturnPath('\\\\attacker.example'), null);
  assert.equal(safeReturnPath('bands'), null);
});
