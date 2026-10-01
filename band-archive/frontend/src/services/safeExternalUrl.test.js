import assert from 'node:assert/strict';
import test from 'node:test';
import { safeExternalHttpUrl } from './safeExternalUrl.js';

test('safe external URLs normalize absolute HTTP(S) destinations only', () => {
  assert.equal(safeExternalHttpUrl('https://example.com/reference?q=band'), 'https://example.com/reference?q=band');
  assert.equal(safeExternalHttpUrl('http://example.com'), 'http://example.com/');
  assert.equal(safeExternalHttpUrl('HTTPS://EXAMPLE.COM/a'), 'https://example.com/a');
});

test('safe external URLs reject unsafe, ambiguous, malformed, and oversized destinations', () => {
  const rejected = [
    'javascript:alert(1)', 'data:text/html,boom', 'file:///secret', 'blob:https://example.com/id',
    '//example.com/path', '/relative', 'relative/path', 'https://user:pass@example.com/path',
    'https://example.com/has space', 'https://example.com/%0aheader', 'https://example.com/%250aheader',
    'https:\\example.com\\path', 'https:////example.com', 'https://', 'https://[bad-host', `https://example.com/${'a'.repeat(2050)}`,
  ];
  rejected.forEach((value) => assert.equal(safeExternalHttpUrl(value), null, value));
});
