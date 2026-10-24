import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import process from 'node:process';
import test from 'node:test';
import { createServer } from 'vite';

// The regression runner supplies ANYTHING_ISOLATED_TEST=1. Synthetic public
// Vite configuration below never loads dotenv files or calls a real API.
for (const { name, env, expected } of [
  { name: 'unset API URL', env: {}, expected: 'http://localhost:5000' },
  { name: 'empty API URL', env: { VITE_API_URL: '' }, expected: 'http://localhost:5000' },
  { name: 'configured API URL', env: { VITE_API_URL: 'https://api.example.test' }, expected: 'https://api.example.test' },
  { name: 'configured URL retains trailing slash', env: { VITE_API_URL: 'https://api.example.test/base/' }, expected: 'https://api.example.test/base/' },
]) {
  test(`neutral API configuration preserves ${name} and its legacy export`, async () => {
    const previousApiUrl = process.env.VITE_API_URL;
    let vite;
    try {
      // SSR injects Vite's resolved public env, rather than the client-side
      // define replacement. Set only this public key for this isolated server.
      if (Object.hasOwn(env, 'VITE_API_URL')) process.env.VITE_API_URL = env.VITE_API_URL;
      else delete process.env.VITE_API_URL;
      vite = await createServer({
        configFile: false,
        envDir: false,
        server: { middlewareMode: true },
        appType: 'custom',
        optimizeDeps: { noDiscovery: true },
      });
      const configuration = await vite.ssrLoadModule('/src/services/apiConfig.js');
      const legacy = await vite.ssrLoadModule('/src/services/api.js');
      const { createAuthenticatedApiClient } = await vite.ssrLoadModule('/src/services/authApiClient.js');
      assert.equal(configuration.API_URL, expected);
      assert.equal(legacy.API_URL, configuration.API_URL);
      const calls = [];
      const api = createAuthenticatedApiClient({
        fetchImpl: async (url, options) => {
          calls.push({ url, options });
          return { ok: true, status: 200, json: async () => ({ bands: [] }) };
        },
      });
      assert.deepEqual(await api.requestJson('/api/v2/public/bands', { authenticated: false }), { bands: [] });
      assert.equal(calls.length, 1);
      assert.equal(calls[0].url, `${expected}/api/v2/public/bands`);
      assert.equal(calls[0].options.headers.Authorization, undefined);
    } finally {
      try { await vite?.close(); }
      finally {
        if (previousApiUrl === undefined) delete process.env.VITE_API_URL;
        else process.env.VITE_API_URL = previousApiUrl;
      }
    }
  });
}

test('neutral API configuration has no legacy API dependency and URL-only consumers use it directly', async () => {
  const configuration = await readFile(new URL('./apiConfig.js', import.meta.url), 'utf8');
  assert.doesNotMatch(configuration, /^\s*import\s/m);
  assert.match(configuration, /import\.meta\.env\?\.VITE_API_URL \|\| 'http:\/\/localhost:5000'/);
  for (const path of ['./authApiClient.js', './inviteLinks.js', './mediaUploadManager.js', '../context/ServicePolicyContext.jsx', '../hooks/useMediaUpload.js']) {
    const source = await readFile(new URL(path, import.meta.url), 'utf8');
    assert.match(source, /import \{ API_URL \} from ['"][^'"]*apiConfig\.js['"]/);
    assert.doesNotMatch(source, /import \{ API_URL \} from ['"][^'"]*\/api(?:\.js)?['"]/);
  }
});
