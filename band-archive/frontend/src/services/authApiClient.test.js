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

test('concurrent 401 responses share one provider refresh and each retry once', async () => {
  let refreshes = 0;
  let releaseRefresh;
  let refreshStarted;
  const refreshGate = new Promise((resolve) => { releaseRefresh = resolve; });
  const refreshStartedGate = new Promise((resolve) => { refreshStarted = resolve; });
  const attempts = new Map();
  const refreshSession = async () => {
    refreshes += 1;
    refreshStarted();
    await refreshGate;
    return 'shared-refreshed-token';
  };
  const options = {
    apiUrl: 'https://api.example',
    getAccessToken: async () => 'expired-token',
    refreshSession,
    fetchImpl: async (url, options) => {
      const attempt = (attempts.get(url) || 0) + 1;
      attempts.set(url, attempt);
      return attempt === 1 ? response(401) : response(200, { token: options.headers.Authorization });
    },
  };
  const api = createAuthenticatedApiClient(options);
  const anotherApiForSameProvider = createAuthenticatedApiClient(options);

  const first = api.requestJson('/api/v2/me');
  const second = anotherApiForSameProvider.requestJson('/api/v2/bands');
  await refreshStartedGate;
  assert.equal(refreshes, 1);
  releaseRefresh();
  assert.deepEqual(await first, { token: 'Bearer shared-refreshed-token' });
  assert.deepEqual(await second, { token: 'Bearer shared-refreshed-token' });
  assert.deepEqual([...attempts.values()], [2, 2]);
});

test('a failed refresh produces one consistent auth error and does not retry requests', async () => {
  let calls = 0;
  const api = createAuthenticatedApiClient({
    apiUrl: 'https://api.example',
    getAccessToken: async () => 'expired-token',
    refreshSession: async () => { throw new Error('transport failed'); },
    fetchImpl: async () => { calls += 1; return response(401); },
  });

  await assert.rejects(() => api.requestJson('/api/v2/me'), (error) => error.status === 401 && error.message === '로그인이 필요합니다. 다시 로그인해 주세요.');
  assert.equal(calls, 1);
});

test('a completed or failed refresh is cleared so a later 401 starts a new refresh', async () => {
  let refreshes = 0;
  let first = true;
  const api = createAuthenticatedApiClient({
    apiUrl: 'https://api.example',
    getAccessToken: async () => 'expired-token',
    refreshSession: async () => {
      refreshes += 1;
      if (first) { first = false; throw new Error('first refresh fails'); }
      return 'later-token';
    },
    fetchImpl: async (_url, options) => options.headers.Authorization === 'Bearer later-token' ? response(200, { ok: true }) : response(401),
  });

  await assert.rejects(() => api.requestJson('/api/v2/me'), (error) => error.status === 401);
  assert.deepEqual(await api.requestJson('/api/v2/me'), { ok: true });
  assert.equal(refreshes, 2);
});
