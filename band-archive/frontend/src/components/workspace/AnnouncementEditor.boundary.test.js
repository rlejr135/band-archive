import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('announcement management imports its editor directly without the workspace implementation', async () => {
  const management = await readFile(new URL('../bands/BandOnboarding.jsx', import.meta.url), 'utf8');
  assert.match(management, /import \{ AnnouncementEditor \} from '\.\.\/workspace\/announcements\/AnnouncementEditor\.jsx'/);
  assert.doesNotMatch(management, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
});

test('standalone announcement editor owns its stylesheet and preserves the workspace export contract', async () => {
  const editor = await readFile(new URL('./announcements/AnnouncementEditor.jsx', import.meta.url), 'utf8');
  const workspace = await readFile(new URL('./BandWorkspace.jsx', import.meta.url), 'utf8');
  assert.match(editor, /import '\.\.\/BandWorkspace\.css'/);
  assert.doesNotMatch(editor, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
  assert.match(editor, /export const AnnouncementEditor = /);
  assert.match(workspace, /import \{ AnnouncementEditor \} from '\.\/announcements\/AnnouncementEditor\.jsx'/);
  assert.match(workspace, /export \{ AnnouncementEditor \}/);
  assert.doesNotMatch(workspace, /export const AnnouncementEditor = /);
});
