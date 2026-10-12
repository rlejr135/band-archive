import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

test('account hub reads memberships once and has no legacy claim or profile request', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ bands: [], pending_memberships: [] }); };
  const authValue = { user: { id: 'member-1' }, getAccessToken: async () => 'member-token', refreshSession: async () => 'member-token', loading: false, signOut: async () => {} };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue },
      createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage)))));
    await view.findByText('내 밴드');
    assert.equal(calls.length, 1);
    assert.match(calls[0], /\/api\/v2\/bands$/);
    assert.equal(calls.some((url) => url.includes('/api/v2/me') || url.includes('legacy-ownership-claim')), false);
    assert.equal(view.queryByText(/소유권/), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('account switch hides prior pending memberships and ignores a late response from the old account', async () => {
  await load(); const previousFetch = globalThis.fetch; const accountA = deferred(); const accountB = deferred(); const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return calls.length === 1 ? accountA.promise : accountB.promise; };
  const renderAccount = (id) => createElement(auth.AuthContext.Provider, { value: { user: { id }, getAccessToken: async () => id, refreshSession: async () => id, loading: false, signOut: async () => {} } },
    createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage))));
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
  const renderAccount = (id) => createElement(auth.AuthContext.Provider, { value: { user: { id }, getAccessToken: async () => id, refreshSession: async () => id, loading: false, signOut: async () => {} } },
    createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage))));
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

test('logout and login to the same user creates a fresh membership request boundary', async () => {
  await load(); const previousFetch = globalThis.fetch; const first = deferred(); const second = deferred(); let reads = 0;
  globalThis.fetch = async (url) => {
    if (!String(url).endsWith('/api/v2/bands')) return response({});
    reads += 1; return reads === 1 ? first.promise : second.promise;
  };
  const renderAccount = (id) => createElement(auth.AuthContext.Provider, { value: { user: id ? { id } : null, getAccessToken: async () => id, refreshSession: async () => id, loading: false, signOut: async () => {} } },
    createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage))));
  try {
    const view = render(renderAccount('same-user'));
    await waitFor(() => assert.equal(reads, 1));
    view.rerender(renderAccount(null));
    view.rerender(renderAccount('same-user'));
    await waitFor(() => assert.equal(reads, 2));
    await act(async () => first.resolve(response({ bands: [{ id: 1, name: 'Old session band', slug: 'old', membership: { role: 'member' } }], pending_memberships: [] })));
    assert.equal(view.queryByText('Old session band'), null);
    await act(async () => second.resolve(response({ bands: [{ id: 2, name: 'New session band', slug: 'new', membership: { role: 'member' } }], pending_memberships: [] })));
    assert.ok(view.getByRole('button', { name: /New session band/ }));
    assert.equal(view.queryByText('Old session band'), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('invite join forces a fresh membership read and ignores the pre-join response', async () => {
  await load(); const previousFetch = globalThis.fetch; const beforeJoin = deferred(); const calls = [];
  globalThis.fetch = async (url) => {
    const path = String(url); calls.push(path);
    if (path.includes('/api/v2/invite-codes/join')) return response({ membership: { id: 24 } });
    const membershipReads = calls.filter((item) => item.endsWith('/api/v2/bands')).length;
    return membershipReads === 1 ? beforeJoin.promise : response({ bands: [{ id: 24, name: 'Joined Band', slug: 'joined-band', membership: { role: 'member' } }], pending_memberships: [] });
  };
  const authValue = { user: { id: 'member-join' }, getAccessToken: async () => 'member-token', refreshSession: async () => 'member-token', loading: false, signOut: async () => {} };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage)))));
    const input = await view.findByLabelText('초대 코드');
    await userEvent.type(input, 'one-time-code');
    await userEvent.click(view.getByRole('button', { name: '가입' }));
    await waitFor(() => assert.ok(calls.some((path) => path.includes('/api/v2/invite-codes/join'))));
    await view.findByText(/목록을 새로 불러옵니다/);
    await view.findByRole('button', { name: /Joined Band/ });
    await waitFor(() => assert.equal(calls.filter((item) => item.endsWith('/api/v2/bands')).length, 2));
    await act(async () => beforeJoin.resolve(response({ bands: [{ id: 7, name: 'Stale Band', slug: 'stale-band', membership: { role: 'member' } }], pending_memberships: [] })));
    assert.ok(view.getByRole('button', { name: /Joined Band/ }));
    assert.equal(view.queryByText('Stale Band'), null);
  } finally { globalThis.fetch = previousFetch; }
});
