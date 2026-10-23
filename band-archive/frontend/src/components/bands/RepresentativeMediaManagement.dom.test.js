import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};

const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const operator = { user: { id: 'platform-operator' }, getAccessToken: async () => 'operator-token', refreshSession: async () => 'operator-token', signOut: async () => {} };
let vite; let management; let auth;
const load = async () => {
  if (management) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  management = (await vite.ssrLoadModule('/src/components/bands/RepresentativeMediaManagement.jsx')).RepresentativeMediaManagementRoute;
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
};
const app = (entry, authValue = operator) => createElement(auth.AuthContext.Provider, { value: authValue },
  createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null,
    createElement(Route, { path: '/bands/:bandSlug/manage/representative-media', element: createElement(management) }))));

after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());

test('private-band superadmin management uses only narrow metadata and candidate reads and supports set and clear', async () => {
  await load();
  const previousFetch = globalThis.fetch;
  const calls = [];
  let featuredId = 1001;
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); const method = options.method || 'GET';
    calls.push({ path, method, body: options.body });
    if (path.includes('/by-slug/private-archive/manage/representative-media?')) {
      return response({ band: { id: 812, slug: 'private-archive', name: '비공개 아카이브', can_feature: true },
        songs: [{ id: 923, title: '숨은 파도', artist: '아카이브 밴드', can_feature: true }],
        pagination: { limit: 20, offset: 0, total: 1, has_more: false, next_offset: null } });
    }
    if (path.endsWith('/songs/923/featured-media/candidates?limit=20&offset=0')) {
      return response({ media: [
        { id: 1001, song_id: 923, file_type: 'audio', display_name: '비공개 원본.m4a', is_featured: featuredId === 1001, can_feature: true },
        { id: 1002, song_id: 923, file_type: 'video', display_name: '새 공연 영상.mp4', is_featured: featuredId === 1002, can_feature: true },
      ], pagination: { limit: 20, offset: 0, total: 2, has_more: false, next_offset: null } });
    }
    if (path.endsWith('/songs/923/featured-media') && method === 'DELETE') { featuredId = null; return response({ song_id: 923, is_featured: false, can_feature: true }); }
    if (path.endsWith('/songs/923/featured-media') && method === 'PUT') { assert.deepEqual(JSON.parse(options.body), { media_id: 1002 }); featuredId = 1002; return response({ song_id: 923, media_id: 1002, is_featured: true, can_feature: true }); }
    throw new Error(`unexpected ${method} ${path}`);
  };

  try {
    const view = render(app('/bands/private-archive/manage/representative-media'));
    await view.findByRole('heading', { name: '대표 미디어 관리' });
    assert.equal((await view.findByRole('option', { name: '숨은 파도 · 아카이브 밴드' })).value, '923');
    fireEvent.change(view.getByRole('combobox', { name: '관리할 곡' }), { target: { value: '923' } });
    await view.findByText('비공개 원본.m4a');
    assert.equal(view.getByRole('button', { name: '대표 해제' }).disabled, false);
    assert.equal(view.queryByRole('button', { name: '재생 미리보기' }), null);
    assert.equal(view.container.querySelector('video, audio'), null);

    await userEvent.click(view.getByRole('button', { name: '대표 해제' }));
    await waitFor(() => assert.equal(view.queryByRole('button', { name: '대표 해제' }), null));
    await userEvent.click(view.getAllByRole('button', { name: '대표로 지정' })[1]);
    await waitFor(() => assert.equal(calls.filter((call) => call.method === 'PUT').length, 1));
    await view.findByRole('button', { name: '대표 해제' });

    const paths = calls.map((call) => call.path.replace(/^https?:\/\/[^/]+/, ''));
    assert.ok(paths.some((path) => path === '/api/v2/bands/by-slug/private-archive/manage/representative-media?limit=20&offset=0'));
    assert.ok(paths.some((path) => path === '/api/v2/bands/812/songs/923/featured-media/candidates?limit=20&offset=0'));
    assert.deepEqual(calls.filter((call) => call.method === 'DELETE' || call.method === 'PUT').map((call) => [call.method, call.path.replace(/^https?:\/\/[^/]+/, ''), call.body]), [
      ['DELETE', '/api/v2/bands/812/songs/923/featured-media', undefined],
      ['PUT', '/api/v2/bands/812/songs/923/featured-media', JSON.stringify({ media_id: 1002 })],
    ]);
    assert.equal(calls.filter((call) => call.path.includes('/manage/representative-media?')).length, 1, 'feature changes refresh candidates, not the unchanged management catalog');
    assert.equal(paths.some((path) => /\/bands\/812\/(?:featured-media|songs(?:\?|\/\d+\/media)|media\/)/.test(path)), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('loading another song page preserves a selection from the earlier page', async () => {
  await load();
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const path = String(url);
    if (!path.includes('/manage/representative-media?')) throw new Error(`unexpected ${path}`);
    const offset = new URL(path).searchParams.get('offset');
    const firstPage = offset === '0';
    return response({ band: { id: 812, slug: 'private-archive', name: '비공개 아카이브', can_feature: true },
      songs: firstPage ? [{ id: 923, title: '숨은 파도', artist: '밴드', can_feature: true }] : [{ id: 924, title: '먼 바다', artist: '밴드', can_feature: true }],
      pagination: { limit: 20, offset: Number(offset), total: 21, has_more: firstPage, next_offset: firstPage ? 20 : null } });
  };
  try {
    const view = render(app('/bands/private-archive/manage/representative-media'));
    await view.findByRole('option', { name: '숨은 파도 · 밴드' });
    fireEvent.change(view.getByRole('combobox', { name: '관리할 곡' }), { target: { value: '923' } });
    await userEvent.click(view.getByRole('button', { name: '곡 더 불러오기' }));
    await view.findByRole('option', { name: '먼 바다 · 밴드' });
    assert.equal(view.getByRole('combobox', { name: '관리할 곡' }).value, '923');
  } finally { globalThis.fetch = previousFetch; }
});

test('an in-flight management mutation cannot reload a previous account after identity change', async () => {
  await load();
  const previousFetch = globalThis.fetch;
  const delayedWrite = deferred(); const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); const method = options.method || 'GET'; calls.push({ path, method });
    if (path.includes('/manage/representative-media?')) return response({ band: { id: 812, slug: 'private-archive', name: '비공개 아카이브', can_feature: true }, songs: [{ id: 923, title: '숨은 파도', artist: '밴드', can_feature: true }], pagination: { limit: 20, offset: 0, total: 1, has_more: false } });
    if (path.endsWith('/songs/923/featured-media/candidates?limit=20&offset=0')) return response({ media: [{ id: 1002, song_id: 923, file_type: 'video', display_name: '공연 영상.mp4', is_featured: false, can_feature: true }], pagination: { limit: 20, offset: 0, total: 1, has_more: false } });
    if (path.endsWith('/songs/923/featured-media') && method === 'PUT') return delayedWrite.promise;
    throw new Error(`unexpected ${method} ${path}`);
  };
  try {
    const view = render(app('/bands/private-archive/manage/representative-media'));
    await view.findByRole('option', { name: '숨은 파도 · 밴드' });
    fireEvent.change(view.getByRole('combobox', { name: '관리할 곡' }), { target: { value: '923' } });
    await userEvent.click(await view.findByRole('button', { name: '대표로 지정' }));
    await waitFor(() => assert.equal(calls.filter((call) => call.method === 'PUT').length, 1));
    const candidatesBeforeSwitch = calls.filter((call) => call.path.includes('/featured-media/candidates')).length;
    view.rerender(app('/bands/private-archive/manage/representative-media', { ...operator, user: { id: 'platform-operator-2' }, authGeneration: 1 }));
    await view.findByRole('option', { name: '숨은 파도 · 밴드' });
    const callsAfterSwitch = calls.length;
    await new Promise((resolve) => setTimeout(resolve, 0));
    delayedWrite.resolve(response({ song_id: 923, media_id: 1002, is_featured: true, can_feature: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(calls.filter((call) => call.path.includes('/featured-media/candidates')).length, candidatesBeforeSwitch, 'late completion does not issue a stale candidate read');
    assert.equal(calls.length, callsAfterSwitch, 'late completion does not trigger an old catalog or candidate reload');
  } finally { globalThis.fetch = previousFetch; }
});

test('management route shows the server denial without falling back to ordinary band reads', async () => {
  await load();
  const previousFetch = globalThis.fetch;
  const paths = [];
  globalThis.fetch = async (url) => { paths.push(String(url)); return response({ code: 'forbidden' }, 403); };
  try {
    const view = render(app('/bands/private-archive/manage/representative-media'));
    await view.findByText('이 밴드의 대표 미디어를 관리할 권한이 없습니다.');
    assert.equal(paths.some((path) => /\/api\/v2\/bands\/\d+\/(?:songs|featured-media)/.test(path)), false);
  } finally { globalThis.fetch = previousFetch; }
});
