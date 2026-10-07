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

const account = (id) => ({ user: { id }, getAccessToken: async () => id, refreshSession: async () => id, loading: false });
const bandValue = { activeBands: [], selectedBand: null, setAvailableBands: () => {}, selectBand: () => {} };
const App = ({ authValue }) => createElement(
  auth.AuthContext.Provider, { value: authValue },
  createElement(bands.BandContext.Provider, { value: bandValue },
    createElement(MemoryRouter, null, createElement(page.BandsPage)),
  ),
);

test('account switch hides platform controls before the next account profile has loaded', async () => {
  await load(); const previousFetch = globalThis.fetch; const accountBMe = deferred(); const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url); const token = options.headers?.Authorization; calls.push({ value, token });
    if (value.endsWith('/api/v2/me')) return token === 'Bearer account-b' ? accountBMe.promise : response({ user: { id: 'account-a', is_platform_admin: true } });
    if (value.endsWith('/api/v2/bands')) return response({ bands: [], pending_memberships: [] });
    if (value.includes('/api/v2/public/bands')) return response({ bands: [], pagination: { total: 0, has_more: false } });
    if (value.includes('/api/v2/platform/legacy-ownership-claims/eligible-bands')) return response({ bands: [{ id: 9, name: 'Legacy', slug: 'legacy' }] });
    throw new Error(`unexpected ${value}`);
  };
  try {
    const view = render(createElement(App, { authValue: account('account-a') }));
    await view.findByRole('button', { name: '소유권 토큰 발급' });

    view.rerender(createElement(App, { authValue: account('account-b') }));
    await waitFor(() => assert.equal(view.queryByRole('button', { name: '소유권 토큰 발급' }), null));
    await waitFor(() => assert.equal(calls.filter((call) => call.value.includes('eligible-bands') && call.token === 'Bearer account-b').length, 0));

    await act(async () => { accountBMe.resolve(response({ user: { id: 'account-b', is_platform_admin: false } })); });
    await view.findByText('활성 멤버십');
    assert.equal(view.queryByRole('button', { name: '소유권 토큰 발급' }), null);
  } finally { globalThis.fetch = previousFetch; }
});
