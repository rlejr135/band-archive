import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { bandWorkspacePaths } from './bandWorkspaceApi.js';

test('current place picker uses authenticated band-scoped search and keeps manual entry available', async () => {
  const [picker, form] = await Promise.all([
    readFile(new URL('../components/workspace/BandPlacePicker.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/workspace/rehearsals/RehearsalForm.jsx', import.meta.url), 'utf8'),
  ]);
  assert.match(picker, /api\.requestJson\(bandWorkspacePaths\.searchPlaces\(bandId, searchQuery\), \{ signal: controller\.signal \}\)/);
  assert.doesNotMatch(picker, /API_URL|\bfetch\s*\(|\/api\/search-places|authenticated:\s*false/);
  assert.match(picker, /장소는 직접 입력해도 됩니다/);
  assert.match(form, /<BandPlacePicker bandId=\{band\.id\}/);
  assert.match(form, /name="location"/);
  assert.equal(bandWorkspacePaths.searchPlaces(7, '홍대 연습실'), '/api/v2/bands/7/search-places?query=%ED%99%8D%EB%8C%80%20%EC%97%B0%EC%8A%B5%EC%8B%A4');
});
