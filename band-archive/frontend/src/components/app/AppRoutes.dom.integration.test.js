import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { cleanup, render, waitFor } from '@testing-library/react';
import { createElement, StrictMode } from 'react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { fireEvent } from '@testing-library/react';
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
const AccountBands = ({ children, authValue, entry, strict = false }) => {
  const tree = createElement(auth.AuthContext.Provider, { value: authValue },
    createElement(bands.BandProvider, null,
      createElement(uploads.UploadProvider, null,
        createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Location), children))));
  return strict ? createElement(StrictMode, null, tree) : tree;
};
const account = (id = null) => ({ user: id ? { id, email: 'person@example.test' } : null, getAccessToken: async () => 'test-token', refreshSession: async () => 'test-token', signOut: async () => {}, loading: false, configError: null, hasRecoverySession: false });
const renderRoutes = (entry, authValue = account(), strict = false) => render(createElement(AccountBands, { entry, authValue, strict }, createElement(routes)));

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

test('platform request route requires login and denies an authenticated non-admin before reading the queue', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ user: { is_platform_admin: false } }); };
  try {
    const anonymous = renderRoutes('/platform/band-requests', account());
    await anonymous.findByRole('heading', { name: '로그인' });
    assert.equal(calls.length, 0);
    anonymous.unmount();

    const member = renderRoutes('/platform/band-requests', account('member'));
    await member.findByText('플랫폼 관리자만 이 페이지를 사용할 수 있습니다.');
    assert.ok(calls.some((url) => url.endsWith('/api/v2/me')));
    assert.equal(calls.some((url) => url.includes('/api/v2/platform/band-creation-requests?')), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('authenticated root opens the account hub with one bands request and no profile or claim calls', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ bands: [], pending_memberships: [] }); };
  try {
    const view = renderRoutes('/', account('member-1'), true);
    await view.findByText('내 밴드');
    assert.equal(calls.filter((url) => url.endsWith('/api/v2/bands')).length, 1);
    assert.ok(calls.some((url) => url.endsWith('/api/v2/me')));
    assert.equal(calls.some((url) => url.includes('legacy-ownership-claim')), false);
    assert.equal(view.queryByText(/소유권/), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('band choice selects the membership and navigates to its slug in the same action', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  const team = { id: 12, name: 'Moon Archive', slug: 'moon-archive', membership: { role: 'owner' } };
  globalThis.fetch = async (url) => { calls.push(String(url)); return response(String(url).includes('/api/v2/bands/by-slug/') ? { band: team } : { bands: [team], pending_memberships: [] }); };
  try {
    const view = renderRoutes('/bands', account('member-1'));
    await view.findByRole('button', { name: /Moon Archive.*소유자/ });
    await view.getByRole('button', { name: /Moon Archive.*소유자/ }).click();
    await waitFor(() => assert.equal(view.getByTestId('route').textContent, '/bands/moon-archive'));
    assert.equal(window.localStorage.getItem('band-archive.selected-band:member-1'), '12');
    assert.ok(calls.some((url) => url.includes('/api/v2/bands/by-slug/moon-archive')));
  } finally { globalThis.fetch = previousFetch; }
});

test('one-band account switcher starts unselected and opens the sole membership', async () => {
  await load(); const previousFetch = globalThis.fetch; const team = { id: 12, name: 'Moon Archive', slug: 'moon-archive', membership: { role: 'owner' } };
  globalThis.fetch = async (url) => response(String(url).includes('/api/v2/bands/by-slug/') ? { band: team } : { bands: [team], pending_memberships: [] });
  try {
    const view = renderRoutes('/bands', account('one-band-user'));
    const switcher = await view.findByRole('combobox', { name: '빠른 밴드 전환' });
    assert.equal(switcher.value, '');
    fireEvent.change(switcher, { target: { value: '12' } });
    await waitFor(() => assert.equal(view.getByTestId('route').textContent, '/bands/moon-archive'));
  } finally { globalThis.fetch = previousFetch; }
});

test('workspace navigation stays on one horizontal scroll row at narrow widths', () => {
  const css = readFileSync(new URL('../workspace/BandWorkspace.css', import.meta.url), 'utf8');
  assert.match(css, /\.workspace-header nav\{[^}]*flex-wrap:nowrap[^}]*overflow-x:auto/);
  assert.match(css, /@media \(max-width: 900px\)/);
  assert.match(css, /\.workspace-actions \.band-switcher\{flex:0 0 142px\}/);
  assert.doesNotMatch(css, /@media[^}]*workspace-header nav[^}]*display:\s*none/);
});

test('a direct workspace link keeps its URL band selected and switches without a second membership read', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  const direct = { id: 21, name: 'Direct Band', slug: 'direct-band', membership: { role: 'owner' }, permissions: { can_view_roster: true } };
  const other = { id: 22, name: 'Other Band', slug: 'other-band', membership: { role: 'member' }, permissions: { can_view_roster: true } };
  globalThis.fetch = async (url) => {
    const path = String(url); calls.push(path);
    if (path.endsWith('/api/v2/bands')) return response({ bands: [direct, other], pending_memberships: [] });
    if (path.includes('/api/v2/bands/by-slug/direct-band')) return response({ band: direct });
    if (path.includes('/api/v2/bands/by-slug/other-band')) return response({ band: other });
    if (path.includes('/api/v2/bands/21/songs')) return response({ songs: [], pagination: { total: 0 } });
    if (path.includes('/api/v2/bands/21/rehearsals')) return response({ rehearsals: [], pagination: { total: 0 } });
    if (path.includes('/api/v2/bands/21/announcement')) return response({ announcement: null });
    return response({});
  };
  window.localStorage.setItem('band-archive.selected-band:direct-link-user', '22');
  try {
    const view = renderRoutes('/bands/direct-band/songs', account('direct-link-user'));
    const switcher = await view.findByRole('combobox', { name: '빠른 밴드 전환' });
    await waitFor(() => assert.equal(switcher.value, '21'));
    fireEvent.change(switcher, { target: { value: '22' } });
    await waitFor(() => assert.equal(view.getByTestId('route').textContent, '/bands/other-band'));
    assert.equal(calls.filter((path) => path.endsWith('/api/v2/bands')).length, 1);
    assert.ok(calls.some((path) => path.includes('/api/v2/bands/by-slug/other-band')));
  } finally { globalThis.fetch = previousFetch; }
});

test('legacy routes go to the landing, while the singular band alias retains only its safe workspace path', async () => {
  await load(); const previousFetch = globalThis.fetch;
    globalThis.fetch = async (url) => response(String(url).includes('/api/v2/bands/by-slug/') ? {} : { bands: [], pending_memberships: [] });
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
    await unknownWorkspace.findByText('내 밴드');
    assert.equal(unknownWorkspace.getByTestId('route').textContent, '/bands');
    unknownWorkspace.unmount();
  } finally { globalThis.fetch = previousFetch; }
});
