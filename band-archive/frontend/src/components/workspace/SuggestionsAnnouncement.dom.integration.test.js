import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};
const user = userEvent.setup({ document: dom.window.document });

let vite; let workspace; let auth; let bands;
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const load = async () => { if (workspace) return; vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); workspace = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); bands = await vite.ssrLoadModule('/src/context/BandContext.jsx'); };
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());

const member = (id = 'member') => ({ user: { id }, getAccessToken: async () => `token-${id}`, refreshSession: async () => `token-${id}`, signOut: async () => {} });
const RouteApp = ({ entry, authValue, dashboard = false }) => createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: { activeBands: [] } }, createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) }, dashboard ? createElement(Route, { index: true, element: createElement(workspace.BandDashboard) }) : createElement(Route, { path: 'suggestions', element: createElement(workspace.SuggestionsWorkspace) }))))));

test('member creates, edits, and deletes a suggestion through the routed workspace', async () => {
  await load(); const originalFetch = globalThis.fetch; const originalConfirm = window.confirm; const writes = []; let entries = [];
  window.confirm = () => true;
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    if (url.includes('/by-slug/team')) return response({ band: { id: 41, slug: 'team', name: '팀', default_visibility: 'MEMBERS', membership: { id: 4, role: 'member', status: 'active' } } });
    if (url.includes('/suggestions?') && method === 'GET') return response({ suggestions: entries, pagination: { has_more: false } });
    if (url.includes('/suggestions?') && method === 'POST') { const body = JSON.parse(options.body); writes.push({ method, body }); entries = [{ id: 9, ...body, status: 'pending', vote_score: 0, viewer_vote: 0, can_edit: true, can_delete: true, can_manage: false }]; return response({ suggestion: entries[0] }, 201); }
    if (url.endsWith('/suggestions/9') && method === 'PATCH') { const body = JSON.parse(options.body); writes.push({ method, body }); entries = [{ ...entries[0], ...body }]; return response({ suggestion: entries[0] }); }
    if (url.endsWith('/suggestions/9') && method === 'DELETE') { writes.push({ method }); entries = []; return response({ deleted: true, id: 9 }); }
    throw new Error(`unexpected ${method} ${url}`);
  };
  try {
    const view = render(createElement(RouteApp, { entry: '/bands/team/suggestions', authValue: member() }));
    await view.findByText('표시할 추천곡이 없습니다.');
    await user.click(view.getByRole('button', { name: '추천곡 추가' }));
    await user.type(view.getByLabelText('제목'), '새 곡'); await user.type(view.getByLabelText('아티스트'), '연주자'); await user.type(view.getByLabelText('참고 링크'), 'https://example.test/song'); await user.type(view.getByLabelText('메모'), '연습용');
    await user.click(view.getByRole('button', { name: '저장' })); await view.findByText('새 곡');
    assert.deepEqual(writes[0], { method: 'POST', body: { title: '새 곡', artist: '연주자', link: 'https://example.test/song', memo: '연습용', visibility: 'MEMBERS' } });
    await user.click(view.getByRole('button', { name: '수정' }));
    const title = view.getByLabelText('제목'); await user.clear(title); await user.type(title, '수정 곡'); await user.click(view.getByRole('button', { name: '저장' })); await view.findByText('수정 곡');
    assert.equal(writes[1].method, 'PATCH'); assert.equal(writes[1].body.title, '수정 곡');
    await user.click(view.getByRole('button', { name: '삭제' })); await view.findByText('표시할 추천곡이 없습니다.'); assert.equal(writes[2].method, 'DELETE');
  } finally { globalThis.fetch = originalFetch; window.confirm = originalConfirm; }
});

test('suggestion voting refetches authoritative viewer state, blocks double clicks, retries, and only managers proceed', async () => {
  await load(); const originalFetch = globalThis.fetch; const originalConfirm = window.confirm; const firstVote = deferred(); const writes = []; let reads = 0; let manager = true;
  window.confirm = () => true;
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    if (url.includes('/by-slug/team')) return response({ band: { id: 42, slug: 'team', name: '팀', default_visibility: 'MEMBERS', membership: { id: 4, role: manager ? 'owner' : 'member', status: 'active' } } });
    if (url.includes('/suggestions?')) { reads += 1; return response({ suggestions: [{ id: 10, title: '투표 곡', artist: '밴드', link: 'https://example.test/vote', visibility: 'MEMBERS', status: 'pending', vote_score: reads > 1 ? 1 : 0, viewer_vote: reads > 1 ? 1 : 0, can_edit: false, can_delete: false, can_manage: manager }], pagination: { has_more: false } }); }
    if (url.endsWith('/suggestions/10/vote') && method === 'PUT') { writes.push({ method, body: JSON.parse(options.body) }); return writes.length === 1 ? firstVote.promise : response({ viewer_vote: 1 }); }
    if (url.endsWith('/suggestions/10/proceed') && method === 'POST') { writes.push({ method, body: JSON.parse(options.body) }); return response({ proceeded: true, song: { id: 90 } }, 201); }
    throw new Error(`unexpected ${method} ${url}`);
  };
  try {
    const view = render(createElement(RouteApp, { entry: '/bands/team/suggestions', authValue: member() })); await view.findByText('투표 곡');
    const up = view.getByRole('button', { name: '추천' }); fireEvent.click(up); fireEvent.click(up); await waitFor(() => assert.equal(writes.length, 1)); assert.deepEqual(writes[0], { method: 'PUT', body: { value: 1 } });
    await act(async () => { firstVote.resolve(response({}, 500)); }); await view.findByRole('alert'); assert.equal(up.disabled, false);
    fireEvent.click(up); await waitFor(() => assert.equal(writes.length, 2)); await waitFor(() => assert.equal(view.getByRole('button', { name: '추천' }).getAttribute('aria-pressed'), 'true'));
    await user.click(view.getByRole('button', { name: '곡으로 진행' })); await waitFor(() => assert.deepEqual(writes[2], { method: 'POST', body: { visibility: 'MEMBERS' } })); assert.ok(reads >= 3);
    view.unmount(); manager = false;
    const nonManager = render(createElement(RouteApp, { entry: '/bands/team/suggestions', authValue: member('other') })); await nonManager.findByText('투표 곡'); assert.equal(nonManager.queryByRole('button', { name: '곡으로 진행' }), null); nonManager.unmount();
  } finally { globalThis.fetch = originalFetch; window.confirm = originalConfirm; }
});

test('dashboard announcement is read-only and never exposes announcement mutations', async () => {
  await load(); const originalFetch = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    if (url.includes('/by-slug/notices')) return response({ band: { id: 43, slug: 'notices', name: '공지', default_visibility: 'MEMBERS', can_manage: true, membership: { id: 4, role: 'owner', status: 'active' } } });
    if (url.includes('/bands/43/dashboard?')) return response({ dashboard: { band_id: 43, month: new URL(url, 'http://localhost').searchParams.get('month'), stats: {}, month_rehearsals: { items: [], pagination: { total: 0, has_more: false } }, announcement: { id: 1, content: '기존 공지', visibility: 'PUBLIC' } } });
    if (method !== 'GET') writes.push(method);
    throw new Error(`unexpected ${method} ${url}`);
  };
  try {
    const view = render(createElement(RouteApp, { entry: '/bands/notices', authValue: member(), dashboard: true })); await view.findByText('기존 공지');
    assert.equal(view.queryByRole('button', { name: /공지 작성|공지 수정|저장/ }), null);
    assert.deepEqual(writes, []);
  } finally { globalThis.fetch = originalFetch; }
});

test('a late band response for a previous account cannot paint the current account workspace', async () => {
  await load(); const originalFetch = globalThis.fetch; const firstBand = deferred(); let bandReads = 0;
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/team')) { bandReads += 1; return bandReads === 1 ? firstBand.promise : response({ band: { id: 44, slug: 'team', name: '현재 계정 밴드', membership: { id: 8, role: 'member', status: 'active' } } }); }
    if (url.includes('/suggestions?')) return response({ suggestions: [{ id: 1, title: '현재 계정 추천', artist: '밴드', status: 'pending' }], pagination: { has_more: false } });
    throw new Error(`unexpected ${url}`);
  };
  try {
    const view = render(createElement(RouteApp, { entry: '/bands/team/suggestions', authValue: member('first') })); await waitFor(() => assert.equal(bandReads, 1));
    view.rerender(createElement(RouteApp, { entry: '/bands/team/suggestions', authValue: member('second') })); await view.findByText('현재 계정 추천');
    await act(async () => { firstBand.resolve(response({ band: { id: 1, slug: 'team', name: '이전 계정 밴드', membership: { id: 1, role: 'member', status: 'active' } } })); });
    assert.equal(view.queryByText('이전 계정 밴드'), null); assert.ok(view.getByText('현재 계정 추천'));
  } finally { globalThis.fetch = originalFetch; }
});
