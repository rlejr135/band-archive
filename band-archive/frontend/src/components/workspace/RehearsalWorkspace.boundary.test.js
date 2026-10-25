import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createServer } from 'vite';

test('rehearsal screen has a direct entry and the legacy workspace export stays compatible', async () => {
  const vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  try {
    const [direct, legacy] = await Promise.all([
      vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalWorkspace.jsx'),
      vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx'),
    ]);
    assert.equal(typeof direct.RehearsalWorkspace, 'function');
    assert.equal(legacy.RehearsalWorkspace, direct.RehearsalWorkspace);
  } finally {
    await vite.close();
  }
});

test('song and rehearsal screens own their detail and form composition without shell imports', async () => {
  const files = [
    './songs/SongWorkspace.jsx', './songs/SongDetail.jsx', './songs/SongForm.jsx',
    './rehearsals/RehearsalWorkspace.jsx', './rehearsals/RehearsalDetail.jsx', './rehearsals/RehearsalForm.jsx',
  ];
  const sources = await Promise.all(files.map((path) => readFile(new URL(path, import.meta.url), 'utf8')));
  for (const source of sources) assert.doesNotMatch(source, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
  const shell = await readFile(new URL('./BandWorkspace.jsx', import.meta.url), 'utf8');
  assert.doesNotMatch(shell, /const ContentWorkspace|const ContentDetail|const ContentForm/);
});
