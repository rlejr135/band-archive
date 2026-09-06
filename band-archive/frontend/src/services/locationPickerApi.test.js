import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('location picker reuses the shared API base URL', async () => {
  const source = await readFile(
    new URL('../components/calendar/LocationPicker.jsx', import.meta.url), 'utf8',
  );
  assert.match(source, /import \{ API_URL \} from '..\/..\/services\/api';/);
  assert.doesNotMatch(source, /const API_URL = import\.meta\.env/);
});
