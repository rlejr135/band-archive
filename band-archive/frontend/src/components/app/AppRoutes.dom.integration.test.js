import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { cleanup, render, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, FormData: dom.window.FormData });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
let vite; let routes; let auth; let bands; let uploads;
const load = async () => {
  if (routes) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  routes = (await vite.ssrLoadModule('/src/App.jsx')).AppRoutes;
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
  uploads = await vite.ssrLoadModule('/src/context/UploadContext.jsx');
};
after(async () => { await vite?.close(); dom.window.close(); }); afterEach(() => cleanup());

const Location = () => { const location = useLocation(); return createElement('output', { 'data-testid': 'route' }, `${location.pathname}${location.search}${location.hash}`); };
const AccountBands = ({ children, authValue, bandValue, entry }) => createElement(auth.AuthContext.Provider, { value: authValue },
  createElement(bands.BandContext.Provider, { value: bandValue },
    createElement(uploads.UploadProvider, null,
      createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Location), children))));
const account = (id = null) => ({ user: id ? { id, email: 'person@example.test' } : null, getAccessToken: async () => 'test-token', refreshSession: async () => 'test-token', signOut: async () => {}, loading: false, configError: null, hasRecoverySession: false });
const noBands = { activeBands: [], selectedBand: null, setAvailableBands: () => {}, selectBand: () => {} };
const renderRoutes = (entry, authValue = account(), bandValue = noBands) => render(createElement(AccountBands, { entry, authValue, bandValue }, createElement(routes)));

test('anonymous home is a static example plus the real login form and performs no domain reads', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); throw new Error('anonymous home should not fetch domain data'); };
  try {
    const view = renderRoutes('/', account());
    await view.findByRole('heading', { name: '함께 만든 순간을 오래 남기다' });
    assert.ok(view.getByText(/실제 밴드 정보가 아닌 화면 예시입니다/));
    assert.ok(view.getByLabelText('이메일'));
    assert.equal(view.queryByRole('navigation', { name: '밴드 공간' }), null);
    assert.equal(view.queryByRole('button', { name: /곡 목록|멤버|갤러리|다음 곡 추천/ }), null);
    assert.deepEqual(calls, []);
  } finally { globalThis.fetch = previousFetch; }
});

test('anonymous browser routes are gated before discover or workspace data can mount', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); throw new Error('private route should not fetch'); };
  try {
    for (const path of ['/bands/discover', '/bands/quiet-lantern/songs']) {
      const view = renderRoutes(path, account());
      await view.findByRole('heading', { name: '로그인' });
      await waitFor(() => assert.match(view.getByTestId('route').textContent, /^\/login\?/));
      assert.equal(view.queryByRole('heading', { name: '공개 밴드' }), null);
      assert.equal(view.queryByRole('navigation', { name: '밴드 공간' }), null);
      view.unmount();
    }
    assert.deepEqual(calls, []);
  } finally { globalThis.fetch = previousFetch; }
});

test('authenticated root opens the band chooser with one bands request and no profile or claim calls', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ bands: [], pending_memberships: [] }); };
  try {
    const view = renderRoutes('/', account('member-1'));
    await view.findByText('활성 멤버십');
    assert.equal(calls.length, 1);
    assert.match(calls[0], /\/api\/v2\/bands$/);
    assert.equal(calls.some((url) => url.includes('/api/v2/me') || url.includes('legacy-ownership-claim')), false);
    assert.equal(view.queryByText(/소유권/), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('band choice selects the membership and navigates to its slug in the same action', async () => {
  await load(); const previousFetch = globalThis.fetch; const selected = []; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ bands: [], pending_memberships: [] }); };
  const team = { id: 12, name: 'Moon Archive', slug: 'moon-archive', membership: { role: 'owner' } };
  const bandValue = { ...noBands, activeBands: [team], selectBand: (id) => selected.push(id) };
  try {
    const view = renderRoutes('/bands', account('member-1'), bandValue);
    await view.findByText('활성 멤버십');
    await view.getByRole('button', { name: /Moon Archive owner/ }).click();
    await waitFor(() => assert.equal(view.getByTestId('route').textContent, '/bands/moon-archive'));
    assert.deepEqual(selected, [12]);
    assert.ok(calls.some((url) => url.includes('/api/v2/bands/by-slug/moon-archive')));
  } finally { globalThis.fetch = previousFetch; }
});

test('legacy routes go to the landing, while the singular band alias retains only its safe workspace path', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => response({ bands: [], pending_memberships: [] });
  try {
    for (const path of ['/songs/43', '/members', '/gallery', '/suggestions', '/not-a-route']) {
      const old = renderRoutes(path, account());
      await old.findByRole('heading', { name: '함께 만든 순간을 오래 남기다' });
      assert.equal(old.getByTestId('route').textContent, '/'); old.unmount();
    }

    const alias = renderRoutes('/band/quiet-lantern/songs?sort=recent#top', account());
    await alias.findByRole('heading', { name: '로그인' });
      await waitFor(() => assert.equal(alias.getByTestId('route').textContent, '/login?returnTo=%2Fbands%2Fquiet-lantern%2Fsongs'));
    alias.unmount();

    const previousCalls = [];
    globalThis.fetch = async (url) => { previousCalls.push(String(url)); return response({}); };
    const signedInAlias = renderRoutes('/band/quiet-lantern/songs?sort=recent#top', account('member-1'));
    await waitFor(() => assert.equal(signedInAlias.getByTestId('route').textContent, '/bands/quiet-lantern/songs'));
    await waitFor(() => assert.ok(previousCalls.some((url) => url.includes('/api/v2/bands/by-slug/quiet-lantern'))));
    signedInAlias.unmount();

    globalThis.fetch = async (url) => { previousCalls.push(String(url)); return response({ bands: [], pending_memberships: [] }); };
    const unknownWorkspace = renderRoutes('/bands/quiet-lantern/not-a-tab', account('member-1'));
    await unknownWorkspace.findByText('활성 멤버십');
    assert.equal(unknownWorkspace.getByTestId('route').textContent, '/bands');
    unknownWorkspace.unmount();
  } finally { globalThis.fetch = previousFetch; }
});
