import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../components/auth/AuthPages.jsx', import.meta.url), 'utf8');

test('all async auth forms use the double-submit guard and finally release it', () => {
  for (const name of ['LoginPage', 'SignupPage', 'ForgotPasswordPage', 'ResetPasswordPage']) {
    const start = source.indexOf(`export const ${name}`);
    const next = source.indexOf('export const ', start + 1);
    const section = source.slice(start, next === -1 ? undefined : next);
    assert.match(section, /submission\.tryAcquire\(\)/, name);
    assert.match(section, /catch \{/, name);
    assert.match(section, /finally \{/, name);
    assert.match(section, /submission\.release\(\)/, name);
  }
});

test('auth forms retain label, status, and alert accessibility contracts', () => {
  assert.match(source, /<label>이메일<input type="email"/);
  assert.match(source, /<label>비밀번호<input type="password"/);
  assert.match(source, /role="alert"/);
  assert.match(source, /role="status"/);
  assert.match(source, /aria-busy=\{submitting\}/);
});
