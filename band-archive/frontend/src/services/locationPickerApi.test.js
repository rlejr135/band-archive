import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('legacy location picker no longer calls the closed public place-search endpoint', async () => {
  const source = await readFile(
    new URL('../components/calendar/LocationPicker.jsx', import.meta.url), 'utf8',
  );
  assert.doesNotMatch(source, /API_URL|fetch\s*\(|\/api\/search-places/);
  assert.match(source, /이전 장소 검색은 종료되었습니다\. 장소를 직접 입력해 주세요\./);
});
