import assert from 'node:assert/strict';
import process from 'node:process';
import test from 'node:test';
import { formatInviteCreatedAt } from './bandDateTime.js';

test('invite timestamps display in KST for naive UTC, Zulu, and explicit offsets', () => {
  assert.equal(formatInviteCreatedAt('2026-01-01 00:30:00'), '2026-01-01 09:30 KST');
  assert.equal(formatInviteCreatedAt('2026-01-01T00:30:00Z'), '2026-01-01 09:30 KST');
  assert.equal(formatInviteCreatedAt('2026-01-01T09:30:00+09:00'), '2026-01-01 09:30 KST');
});

test('invite timestamp formatting is independent of the process timezone', () => {
  const previous = process.env.TZ;
  try {
    process.env.TZ = 'Pacific/Honolulu';
    const first = formatInviteCreatedAt('2026-06-15T12:00:00Z');
    process.env.TZ = 'Europe/London';
    assert.equal(formatInviteCreatedAt('2026-06-15T12:00:00Z'), first);
    assert.equal(first, '2026-06-15 21:00 KST');
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});

test('invalid invite timestamps never expose the raw value', () => {
  for (const invalid of [null, '', '2026-02-30T12:00:00Z', 'not a date', '2026-01-01T25:00:00']) {
    assert.equal(formatInviteCreatedAt(invalid), '시간 정보 없음');
  }
});
