import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

test('song screen has a direct entry and its legacy workspace export stays compatible', async () => {
  const vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  try {
    const [direct, legacy] = await Promise.all([
      vite.ssrLoadModule('/src/components/workspace/songs/SongWorkspace.jsx'),
      vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx'),
    ]);
    assert.equal(typeof direct.SongWorkspace, 'function');
    assert.equal(legacy.SongWorkspace, direct.SongWorkspace);
  } finally {
    await vite.close();
  }
});
