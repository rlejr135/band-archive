import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeEmail, safeAuthReturnPath, safeReturnPath } from './authRedirect.js';

test('normalizes email input without altering the password', () => {
  assert.equal(normalizeEmail('  Musician@Example.COM  '), 'musician@example.com');
  assert.equal(normalizeEmail(null), '');
});

test('accepts only same-origin internal return paths', () => {
  assert.equal(safeReturnPath('/'), '/');
  assert.equal(safeReturnPath('/account'), '/account');
  assert.equal(safeReturnPath('/bands'), '/bands');
  assert.equal(safeReturnPath('/bands/rehearsals'), '/bands/rehearsals');
  for (const unsafe of [
    '/login', '/signup', '/verify-email', '/forgot-password', '/reset-password',
    '/songs/42', 'https://attacker.example/bands', '//attacker.example/bands', 'javascript:alert(1)',
    '/bands%2f..%2flogin', '/bands%252f..%252flogin', '/bands\\evil', '/bands//evil',
    '/bands?token=secret', '/bands#token', '/bands\u0000hidden', ' /bands', 'bands',
  ]) assert.equal(safeReturnPath(unsafe), '/', unsafe);
});

test('authenticated login fallback cannot navigate back to an auth route', () => {
  assert.equal(safeReturnPath('/login'), '/');
  assert.equal(safeAuthReturnPath('/portal'), '/');
  assert.equal(safeAuthReturnPath('/account'), '/account');
});

test('invite continuation is the only additional exact auth return path', () => {
  assert.equal(safeAuthReturnPath('/invite/continue'), '/invite/continue');
  for (const value of ['/invite', '/invite/continue/extra', '/invite/continue?next=/bands', '//evil.test/invite/continue']) {
    assert.equal(safeAuthReturnPath(value), '/');
  }
  assert.equal(safeReturnPath('/invite/continue'), '/');
});
