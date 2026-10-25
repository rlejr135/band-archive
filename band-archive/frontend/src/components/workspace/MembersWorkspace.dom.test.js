import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { createElement as h } from 'react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};
let vite; let shell; let detail; let playback; let context; let auth; let bands;
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const response = (payload) => ({ ok: true, status: 200, json: async () => payload });
const authValue = { user: { id: 'account-a' }, getAccessToken: async () => 'token', refreshSession: async () => 'token' };
const band = { id: 41, slug: 'alpha', name: 'Alpha', permissions: { can_view_roster: true }, membership: { role: 'member', status: 'active' } };
const load = async () => {
  if (vite) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  shell = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx');
  detail = await vite.ssrLoadModule('/src/components/workspace/members/MemberDetailWorkspace.jsx');
  playback = await vite.ssrLoadModule('/src/components/workspace/members/PersonalLogPlayback.jsx');
  context = await vite.ssrLoadModule('/src/components/workspace/WorkspaceContext.jsx');
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
};
afterEach(() => cleanup());
after(async () => { await vite?.close(); dom.window.close(); });

const Controls = () => {
  const navigate = useNavigate();
  return h('div', null,
    h('button', { onClick: () => navigate('/bands/alpha/members/2') }, 'select member'),
    h('button', { onClick: () => navigate('/bands/beta/members/1') }, 'select band'));
};
const App = ({ account = authValue, revision = 0 }) => h(auth.AuthContext.Provider, { value: account },
  h(bands.BandContext.Provider, { value: { activeBands: [], membershipRevisions: { alpha: revision } } },
    h(MemoryRouter, { initialEntries: ['/bands/alpha/members/1'] }, h(Controls),
      h(Routes, null, h(Route, { path: '/bands/:bandSlug', element: h(shell.BandWorkspaceRoute) },
        h(Route, { path: 'members/:memberId', element: h(detail.MemberDetailWorkspace) }))))));

for (const boundary of ['member', 'band', 'account', 'membership']) {
  test(`member detail drops late profile and logs after ${boundary} changes`, async () => {
    await load(); const previousFetch = globalThis.fetch;
    const oldProfile = deferred(); const oldLogs = deferred(); const signals = []; let hold = true;
    globalThis.fetch = async (url, options = {}) => {
      const path = String(url);
      if (path.includes('/by-slug/')) return response({ band: path.includes('/beta') ? { ...band, id: 42, slug: 'beta' } : band });
      if (path.includes('/members/1') && hold) {
        signals.push(options.signal);
        return path.includes('/logs?') ? oldLogs.promise : oldProfile.promise;
      }
      if (path.includes('/logs?')) return response({ logs: [{ id: 22, title: 'Fresh log', playback: {} }], pagination: { has_more: false } });
      if (/\/members\/\d+$/.test(path)) return response({ member: { id: 2, name: 'Fresh member', is_self: false } });
      throw new Error(`Unexpected request: ${path}`);
    };
    try {
      const view = render(h(App));
      await waitFor(() => assert.equal(signals.length, 2));
      hold = false;
      if (boundary === 'member' || boundary === 'band') fireEvent.click(view.getByRole('button', { name: `select ${boundary}` }));
      else view.rerender(h(App, boundary === 'account' ? { account: { ...authValue, user: { id: 'account-b' } } } : { revision: 1 }));
      await view.findByText('Fresh member'); await view.findByText('Fresh log');
      assert.ok(signals.every((signal) => signal.aborted));
      await act(async () => { oldProfile.resolve(response({ member: { id: 1, name: 'Obsolete member' } })); oldLogs.resolve(response({ logs: [{ id: 11, title: 'Obsolete log' }], pagination: { has_more: false } })); });
      assert.equal(view.queryByText('Obsolete member'), null); assert.equal(view.queryByText('Obsolete log'), null);
      assert.ok(view.getByText('Fresh member'));
    } finally { globalThis.fetch = previousFetch; }
  });
}

test('personal processing aborts prior selection and unmount and ignores reverse responses', async () => {
  await load(); const first = deferred(); const second = deferred(); const pendingUnmount = deferred(); const signals = [];
  const api = { requestJson: (path, options) => { signals.push(options.signal); return path.includes('/11/') ? first.promise : path.includes('/12/') ? second.promise : pendingUnmount.promise; } };
  const app = (id) => h(auth.AuthContext.Provider, { value: authValue }, h(context.WorkspaceContext.Provider, { value: { band, api, workspaceKey: 'alpha:account-a' } }, h(playback.PersonalLogPlayback, { log: { id, title: 'Selected log', file_type: 'audio', playback: {} }, workspaceKey: 'alpha:account-a', onRefresh: () => {}, onDeleted: () => {} })));
  const view = render(app(11)); await waitFor(() => assert.equal(signals.length, 1));
  view.rerender(app(12)); await waitFor(() => assert.equal(signals.length, 2)); assert.equal(signals[0].aborted, true);
  await act(async () => second.resolve({ processing: { status: 'completed' } }));
  await view.findByText('처리 상태: completed');
  await act(async () => first.resolve({ processing: { status: 'failed' } }));
  assert.equal(view.queryByText(/처리에 실패했습니다/), null);
  view.rerender(app(13)); await waitFor(() => assert.equal(signals.length, 3));
  assert.equal(signals[1].aborted, true);
  view.unmount(); assert.equal(signals[2].aborted, true);
  await act(async () => pendingUnmount.resolve({ processing: { status: 'failed' } }));
  assert.equal(view.container.textContent, '');
});

test('personal log URL refresh replaces the selected ID source and preserves pagination then dedupes appended rows', async () => {
  await load(); const previousFetch = globalThis.fetch; let reads = 0;
  globalThis.fetch = async (url) => {
    const path = String(url);
    if (path.includes('/by-slug/')) return response({ band });
    if (path.endsWith('/members/1')) return response({ member: { id: 1, name: 'Current member' } });
    if (path.includes('/logs?')) {
      if (path.includes('offset=20')) return response({ logs: [{ id: 11, title: 'First log', file_type: 'audio', playback: {} }, { id: 12, title: 'Second log', playback: {} }], pagination: { has_more: false } });
      reads += 1;
      return response({ logs: [{ id: 11, title: 'First log', file_type: 'audio', playback: { audio_url: `https://media.invalid/source-${reads}.m4a` } }], pagination: { has_more: true, next_offset: 20 } });
    }
    if (path.includes('/processing')) return response({ processing: { status: 'completed' } });
    throw new Error(`Unexpected request: ${path}`);
  };
  try {
    const view = render(h(App)); await view.findByText('First log'); fireEvent.click(view.getByText('First log'));
    await waitFor(() => assert.equal(view.container.querySelector('audio')?.getAttribute('src'), 'https://media.invalid/source-1.m4a'));
    fireEvent.error(view.container.querySelector('audio'));
    await waitFor(() => assert.equal(view.container.querySelector('audio')?.getAttribute('src'), 'https://media.invalid/source-2.m4a'));
    fireEvent.error(view.container.querySelector('audio')); assert.equal(reads, 2);
    fireEvent.click(view.getByRole('button', { name: '기록 더 불러오기' })); await view.findByText('Second log');
    assert.equal(view.container.querySelectorAll('.workspace-list li').length, 2);
    assert.ok(view.container.querySelector('.workspace-row.selected'));
  } finally { globalThis.fetch = previousFetch; }
});
