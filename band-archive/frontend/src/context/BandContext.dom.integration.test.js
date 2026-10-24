import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { createElement, StrictMode } from 'react';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });

let vite; let auth; let bands;
const load = async () => {
  if (bands) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
};
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => { cleanup(); window.localStorage.clear(); });

const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
const account = (id) => ({ user: id ? { id } : null, getAccessToken: async () => 'test-token', refreshSession: async () => 'test-token' });
const Probe = () => {
  const { activeBands, pendingMemberships, bandsLoaded, loading, error, selectedBand, reload } = bands.useBands();
  return createElement('div', null,
    createElement('output', { 'data-testid': 'band-state' }, JSON.stringify({ activeBands, pendingMemberships, bandsLoaded, loading, error, selectedBand })),
    createElement('button', { type: 'button', onClick: () => reload() }, '명시적 재조회'));
};
const tree = (authValue) => createElement(StrictMode, null,
  createElement(auth.AuthContext.Provider, { value: authValue },
    createElement(bands.BandProvider, null, createElement(Probe))));
const state = (view) => JSON.parse(view.getByTestId('band-state').textContent);

test('a first empty membership response ends loading, clears stale selection, and does not refetch on render', async () => {
  await load(); const previousFetch = globalThis.fetch; const pending = deferred(); const calls = [];
  window.localStorage.setItem('band-archive.selected-band:empty-account', '81');
  globalThis.fetch = async (url) => { calls.push(String(url)); return pending.promise; };
  try {
    const authValue = account('empty-account');
    const view = render(tree(authValue));
    await waitFor(() => assert.equal(calls.length, 1));
    assert.equal(state(view).loading, true);
    assert.equal(state(view).bandsLoaded, false);
    await act(async () => pending.resolve(response({ bands: [], pending_memberships: [] })));
    await waitFor(() => assert.deepEqual(state(view), { activeBands: [], pendingMemberships: [], bandsLoaded: true, loading: false, error: '', selectedBand: null }));
    assert.equal(window.localStorage.getItem('band-archive.selected-band:empty-account'), null);
    view.rerender(tree(authValue));
    assert.equal(calls.length, 1);
    assert.equal(calls[0].endsWith('/api/v2/bands'), true);
  } finally { globalThis.fetch = previousFetch; }
});

test('an empty account finishes a focus refresh without becoming a loading loop or losing pending requests', async () => {
  await load(); const previousFetch = globalThis.fetch; const refresh = deferred(); let calls = 0;
  const pendingMemberships = [{ id: 9, status: 'pending', band: { id: 81, slug: 'pending-band' } }];
  globalThis.fetch = async () => {
    calls += 1;
    return calls === 1 ? response({ bands: [], pending_memberships: pendingMemberships }) : refresh.promise;
  };
  try {
    const view = render(tree(account('pending-account')));
    await waitFor(() => assert.equal(state(view).bandsLoaded, true));
    await act(async () => window.dispatchEvent(new Event('focus')));
    await waitFor(() => assert.equal(calls, 2));
    assert.equal(state(view).loading, false);
    assert.deepEqual(state(view).pendingMemberships, pendingMemberships);
    await act(async () => refresh.resolve(response({ bands: [], pending_memberships: pendingMemberships })));
    await waitFor(() => assert.equal(state(view).error, ''));
    assert.equal(state(view).loading, false);
    assert.equal(calls, 2);
  } finally { globalThis.fetch = previousFetch; }
});

test('failed membership loading reaches an error state and an explicit retry can recover to an empty account', async () => {
  await load(); const previousFetch = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return calls === 1 ? response({}, 503) : response({ bands: [], pending_memberships: [] });
  };
  try {
    const view = render(tree(account('retry-account')));
    await waitFor(() => assert.equal(state(view).error, '밴드 정보를 불러오지 못했습니다. 다시 시도해 주세요.'));
    assert.equal(state(view).loading, false);
    assert.equal(state(view).bandsLoaded, true);
    assert.equal(calls, 1);
    fireEvent.click(view.getByRole('button', { name: '명시적 재조회' }));
    await waitFor(() => assert.equal(state(view).error, ''));
    await waitFor(() => assert.equal(state(view).bandsLoaded, true));
    assert.equal(state(view).loading, false);
    assert.deepEqual(state(view).activeBands, []);
    assert.equal(calls, 2);
  } finally { globalThis.fetch = previousFetch; }
});

test('a late response from the previous account cannot replace a newly loaded empty membership list', async () => {
  await load(); const previousFetch = globalThis.fetch; const oldRead = deferred(); let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return calls === 1 ? oldRead.promise : response({ bands: [], pending_memberships: [] });
  };
  try {
    const view = render(tree(account('old-account')));
    await waitFor(() => assert.equal(calls, 1));
    view.rerender(tree(account('new-account')));
    await waitFor(() => assert.equal(state(view).bandsLoaded, true));
    assert.equal(calls, 2);
    await act(async () => oldRead.resolve(response({ bands: [{ id: 81, slug: 'old-band', membership: { role: 'owner' } }], pending_memberships: [] })));
    assert.deepEqual(state(view).activeBands, []);
    assert.equal(state(view).selectedBand, null);
    assert.equal(state(view).loading, false);
    assert.equal(window.localStorage.getItem('band-archive.selected-band:new-account'), null);
  } finally { globalThis.fetch = previousFetch; }
});
