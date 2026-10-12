import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, FormData: dom.window.FormData });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
let vite; let auth; let bands; let page;
const load = async () => {
  if (page) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
  page = await vite.ssrLoadModule('/src/components/bands/BandsPage.jsx');
};
after(async () => { await vite?.close(); dom.window.close(); }); afterEach(() => cleanup());

test('band chooser reads the membership list once and has no legacy claim or profile request', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ bands: [], pending_memberships: [] }); };
  const authValue = { user: { id: 'member-1' }, getAccessToken: async () => 'member-token', refreshSession: async () => 'member-token', loading: false, signOut: async () => {} };
  const bandValue = { activeBands: [], selectedBand: null, setAvailableBands: () => {}, selectBand: () => {} };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue },
      createElement(bands.BandContext.Provider, { value: bandValue }, createElement(MemoryRouter, null, createElement(page.BandsPage)))));
    await view.findByText('활성 멤버십');
    assert.equal(calls.length, 1);
    assert.match(calls[0], /\/api\/v2\/bands$/);
    assert.equal(calls.some((url) => url.includes('/api/v2/me') || url.includes('legacy-ownership-claim')), false);
    assert.equal(view.queryByText(/소유권/), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('account switch hides prior pending memberships and ignores a late response from the old account', async () => {
  await load(); const previousFetch = globalThis.fetch; const accountA = deferred(); const accountB = deferred(); const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return calls.length === 1 ? accountA.promise : accountB.promise; };
  const bandValue = { activeBands: [], selectedBand: null, setAvailableBands: () => {}, selectBand: () => {} };
  const renderAccount = (id) => createElement(auth.AuthContext.Provider, { value: { user: { id }, getAccessToken: async () => id, refreshSession: async () => id, loading: false, signOut: async () => {} } },
    createElement(bands.BandContext.Provider, { value: bandValue }, createElement(MemoryRouter, null, createElement(page.BandsPage))));
  try {
    const view = render(renderAccount('account-a'));
    await waitFor(() => assert.equal(calls.length, 1));
    view.rerender(renderAccount('account-b'));
    await waitFor(() => assert.equal(calls.length, 2));
    await act(async () => accountA.resolve(response({ bands: [], pending_memberships: [{ id: 1, band: { name: 'A private pending band' } }] })));
    assert.equal(view.queryByText('A private pending band'), null);
    await act(async () => accountB.resolve(response({ bands: [], pending_memberships: [{ id: 2, band: { name: 'B pending band' } }] })));
    await view.findByText('B pending band', { exact: false });
    assert.equal(view.queryByText('A private pending band'), null);
    assert.equal(calls.length, 2);
  } finally { globalThis.fetch = previousFetch; }
});

test('already-loaded account memberships disappear synchronously while the next account is still loading', async () => {
  await load(); const previousFetch = globalThis.fetch; const accountB = deferred(); const calls = [];
  globalThis.fetch = async () => { calls.push(calls.length + 1); return calls.length === 1
    ? response({ bands: [], pending_memberships: [{ id: 1, band: { name: 'A visible pending band' } }] })
    : accountB.promise; };
  const bandValue = { activeBands: [], selectedBand: null, setAvailableBands: () => {}, selectBand: () => {} };
  const renderAccount = (id) => createElement(auth.AuthContext.Provider, { value: { user: { id }, getAccessToken: async () => id, refreshSession: async () => id, loading: false, signOut: async () => {} } },
    createElement(bands.BandContext.Provider, { value: bandValue }, createElement(MemoryRouter, null, createElement(page.BandsPage))));
  try {
    const view = render(renderAccount('account-a'));
    await view.findByText('A visible pending band', { exact: false });
    view.rerender(renderAccount('account-b'));
    assert.equal(view.queryByText('A visible pending band'), null);
    await waitFor(() => assert.equal(calls.length, 2));
    await act(async () => accountB.resolve(response({ bands: [], pending_memberships: [{ id: 2, band: { name: 'B visible pending band' } }] })));
    await view.findByText('B visible pending band', { exact: false });
    assert.equal(view.queryByText('A visible pending band'), null);
  } finally { globalThis.fetch = previousFetch; }
});
