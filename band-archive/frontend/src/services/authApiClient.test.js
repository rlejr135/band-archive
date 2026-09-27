import assert from 'node:assert/strict';
import test from 'node:test';
import { createAuthenticatedApiClient } from './authApiClient.js';

const response = (status, payload = {}) => ({
  status,
  ok: status >= 200 && status < 300,
  json: async () => payload,
});

test('anonymous calls remain anonymous', async () => {
  const calls = [];
  const api = createAuthenticatedApiClient({
    apiUrl: 'https://api.example',
    fetchImpl: async (url, options) => { calls.push({ url, options }); return response(200, { ok: true }); },
    getAccessToken: async () => 'should-not-be-used',
    refreshSession: async () => 'should-not-be-used',
  });

  await api.requestJson('/public/bands', { authenticated: false });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.headers.Authorization, undefined);
});

test('an authenticated 401 refreshes and retries exactly once', async () => {
  const headers = [];
  let refreshes = 0;
  let token = 'first-token';
  const api = createAuthenticatedApiClient({
    apiUrl: 'https://api.example',
    fetchImpl: async (_url, options) => {
      headers.push(options.headers.Authorization);
      return headers.length === 1 ? response(401) : response(200, { bands: [] });
    },
    getAccessToken: async () => token,
    refreshSession: async () => { refreshes += 1; token = 'refreshed-token'; return token; },
  });

  assert.deepEqual(await api.requestJson('/api/v2/bands'), { bands: [] });
  assert.deepEqual(headers, ['Bearer first-token', 'Bearer refreshed-token']);
  assert.equal(refreshes, 1);
});

test('a second 401 is surfaced instead of creating a retry loop', async () => {
  let calls = 0;
  const api = createAuthenticatedApiClient({
    apiUrl: 'https://api.example',
    fetchImpl: async () => { calls += 1; return response(401); },
    getAccessToken: async () => 'token',
    refreshSession: async () => 'new-token',
  });

  await assert.rejects(() => api.requestJson('/api/v2/me'), (error) => error.status === 401);
  assert.equal(calls, 2);
});
