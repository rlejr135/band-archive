import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeEmail, safeReturnPath } from './authRedirect.js';

test('normalizes email input without altering the password', () => {
  assert.equal(normalizeEmail('  Musician@Example.COM  '), 'musician@example.com');
  assert.equal(normalizeEmail(null), '');
});

test('accepts only same-origin internal return paths', () => {
  assert.equal(safeReturnPath('/bands'), '/bands');
  assert.equal(safeReturnPath('/bands/rehearsals'), '/bands/rehearsals');
  for (const unsafe of [
    '/', '/login', '/signup', '/verify-email', '/forgot-password', '/reset-password',
    '/songs/42', 'https://attacker.example/bands', '//attacker.example/bands', 'javascript:alert(1)',
    '/bands%2f..%2flogin', '/bands%252f..%252flogin', '/bands\\evil', '/bands//evil',
    '/bands?token=secret', '/bands#token', '/bands\u0000hidden', ' /bands', 'bands',
  ]) assert.equal(safeReturnPath(unsafe), '/bands', unsafe);
});

test('authenticated login fallback cannot navigate back to an auth route', () => {
  assert.equal(safeReturnPath('/login'), '/bands');
});
