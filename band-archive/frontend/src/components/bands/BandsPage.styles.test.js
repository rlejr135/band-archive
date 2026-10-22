import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const stylesheet = new URL('./BandsPage.css', import.meta.url);

test('band form controls and placeholders inherit the surrounding typography', async () => {
  const css = await readFile(stylesheet, 'utf8');

  assert.match(css, /\.bands-form input,\.bands-form textarea,\.bands-panel select\s*\{[^}]*width:\s*100%[^}]*padding:\s*9px[^}]*;/);
  assert.match(css, /\.bands-form input,\.bands-form textarea,\.bands-form select\s*\{[^}]*font:\s*inherit\s*;/);
  assert.match(css, /\.bands-form input::placeholder,\.bands-form textarea::placeholder\s*\{\s*font:\s*inherit\s*;\s*\}/);
});
