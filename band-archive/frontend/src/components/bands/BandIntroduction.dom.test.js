import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, FormData: dom.window.FormData });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
let vite; let auth; let component; let appRoutes;
const load = async () => {
  if (component) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  component = await vite.ssrLoadModule('/src/components/bands/BandIntroduction.jsx');
  appRoutes = (await vite.ssrLoadModule('/src/App.jsx')).AppRoutes;
};
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());
const account = (id = null) => ({ user: id ? { id } : null, getAccessToken: async () => id ? `token-${id}` : null, refreshSession: async () => null, loading: false });
const renderIntroduction = (authValue, entry) => render(createElement(auth.AuthContext.Provider, { value: authValue },
  createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null,
    createElement(Route, { path: '/bands/:bandSlug/about', element: createElement(component.BandIntroductionRoute) })))));
const fixture = ({ status = 'active', canOpen = status === 'active', name = '소개 밴드', slug = 'intro-band' } = {}) => ({ introduction: {
  band: { id: 10, name, slug, description: '함께 연주하는 밴드입니다.' },
  featured_gallery: { id: 3, url: 'https://media.example.test/cover.jpg' },
  stats: { total_songs: 12 }, song_count_scope: 'PUBLIC',
  announcement: { content: '다음 합주는 토요일입니다.' },
  viewer: { membership_status: status, can_open_workspace: canOpen },
} });

test('public introduction route works for an anonymous visitor without workspace navigation or writes', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => { calls.push({ url: String(url), options }); return response(fixture({ status: null, canOpen: false })); };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: account() }, createElement(MemoryRouter, { initialEntries: ['/bands/intro-band/about'] }, createElement(appRoutes))));
    await view.findByRole('heading', { name: '소개 밴드' });
    assert.ok(view.getByText('함께 연주하는 밴드입니다.'));
    assert.ok(view.getByText('12', { exact: false }));
    assert.ok(view.getByText('다음 합주는 토요일입니다.'));
    assert.ok(view.getByRole('img', { name: '소개 밴드 대표 사진' }));
    assert.equal(view.queryByRole('navigation'), null);
    assert.equal(view.queryByRole('search'), null);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'http://localhost:5000/api/v2/bands/by-slug/intro-band/introduction');
    assert.equal(calls[0].options.method, undefined);
    assert.equal(calls[0].options.cache, 'no-store');
  } finally { globalThis.fetch = previous; }
});

test('viewer membership states produce only the action allowed by the introduction response', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async () => response(fixture({ status: 'active', canOpen: true }));
  try {
    const view = renderIntroduction(account('member-a'), '/bands/intro-band/about');
    await view.findByRole('link', { name: '밴드 공간 열기' });
    assert.equal(view.queryByRole('button', { name: /가입/ }), null);
    view.unmount();
    globalThis.fetch = async () => response(fixture({ status: 'pending', canOpen: false }));
    const pending = renderIntroduction(account('member-b'), '/bands/intro-band/about');
    await pending.findByRole('status');
    assert.equal(pending.queryByRole('button', { name: /가입/ }), null);
  } finally { globalThis.fetch = previous; }
});

test('not found and authentication failures stay distinct', async () => {
  await load(); const previous = globalThis.fetch;
  try {
    globalThis.fetch = async () => response({}, 404);
    const missing = renderIntroduction(account(), '/bands/gone/about');
    await missing.findByText('이 밴드를 찾을 수 없습니다.'); missing.unmount();
    globalThis.fetch = async () => response({}, 401);
    const unauthorized = renderIntroduction(account('stale'), '/bands/private/about');
    await unauthorized.findByText('로그인 상태를 확인할 수 없습니다. 다시 로그인해 주세요.');
  } finally { globalThis.fetch = previous; }
});

test('malformed or cross-band introduction payloads are not rendered', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async () => response(fixture({ name: '다른 밴드', slug: 'different-band' }));
  try {
    const view = renderIntroduction(account(), '/bands/intro-band/about');
    await view.findByRole('alert');
    assert.equal(view.queryByRole('heading', { name: '다른 밴드' }), null);
  } finally { globalThis.fetch = previous; }
});

test('account switch hides the prior band identity and ignores its late response', async () => {
  await load(); const previous = globalThis.fetch; const first = deferred(); let reads = 0;
  globalThis.fetch = async () => { reads += 1; return reads === 1 ? first.promise : response(fixture({ name: '계정 B 밴드' })); };
  const tree = (id) => createElement(auth.AuthContext.Provider, { value: account(id) }, createElement(MemoryRouter, { initialEntries: ['/bands/intro-band/about'] }, createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug/about', element: createElement(component.BandIntroductionRoute) }))));
  try {
    const view = render(tree('account-a'));
    await waitFor(() => assert.equal(reads, 1));
    view.rerender(tree('account-b'));
    await view.findByRole('heading', { name: '계정 B 밴드' });
    await act(async () => first.resolve(response(fixture({ name: '계정 A 밴드' }))));
    assert.equal(view.queryByRole('heading', { name: '계정 A 밴드' }), null);
  } finally { globalThis.fetch = previous; }
});

test('failed featured image requests a fresh introduction at most once and falls back safely', async () => {
  await load(); const previous = globalThis.fetch; let reads = 0;
  globalThis.fetch = async () => {
    reads += 1;
    if (reads === 1) return response(fixture());
    return response({ introduction: { ...fixture().introduction, featured_gallery: { id: 3, url: 'https://media.example.test/refreshed-cover.jpg' } } });
  };
  try {
    const view = renderIntroduction(account(), '/bands/intro-band/about');
    const image = await view.findByRole('img', { name: '소개 밴드 대표 사진' });
    await act(async () => image.dispatchEvent(new dom.window.Event('error')));
    await waitFor(() => assert.equal(reads, 2));
    const refreshed = await view.findByRole('img', { name: '소개 밴드 대표 사진' });
    assert.equal(refreshed.getAttribute('src'), 'https://media.example.test/refreshed-cover.jpg');
    await act(async () => refreshed.dispatchEvent(new dom.window.Event('error')));
    await view.findByRole('img', { name: '소개 밴드 대표 사진 없음' });
    assert.equal(reads, 2);
  } finally { globalThis.fetch = previous; }
});

test('late featured-photo refresh after account switch cannot clear the new account state', async () => {
  await load(); const previous = globalThis.fetch; const lateRefresh = deferred(); let reads = 0;
  globalThis.fetch = async () => {
    reads += 1;
    if (reads === 1) return response(fixture());
    if (reads === 2) return lateRefresh.promise;
    return response(fixture({ name: '계정 B 밴드' }));
  };
  const tree = (id) => createElement(auth.AuthContext.Provider, { value: account(id) }, createElement(MemoryRouter, { initialEntries: ['/bands/intro-band/about'] }, createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug/about', element: createElement(component.BandIntroductionRoute) }))));
  try {
    const view = render(tree('account-a'));
    const image = await view.findByRole('img', { name: '소개 밴드 대표 사진' });
    await act(async () => image.dispatchEvent(new dom.window.Event('error')));
    await waitFor(() => assert.equal(reads, 2));
    view.rerender(tree('account-b'));
    await view.findByRole('heading', { name: '계정 B 밴드' });
    assert.equal(view.queryByText('사진을 다시 불러오는 중…'), null);
    await act(async () => lateRefresh.resolve(response(fixture({ name: '계정 A의 늦은 사진' }))));
    assert.equal(view.queryByRole('heading', { name: '계정 A의 늦은 사진' }), null);
    assert.equal(view.getByRole('img', { name: '계정 B 밴드 대표 사진' }).getAttribute('src'), 'https://media.example.test/cover.jpg');
  } finally { globalThis.fetch = previous; }
});
