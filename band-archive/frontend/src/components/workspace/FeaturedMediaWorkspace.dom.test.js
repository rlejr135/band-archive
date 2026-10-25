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
let vite; let shell; let featured; let context; let auth; let bands; let upload;
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const response = (payload) => ({ ok: true, status: 200, json: async () => payload });
const authValue = { user: { id: 'account-a' }, getAccessToken: async () => 'token', refreshSession: async () => 'token' };
const band = { id: 41, slug: 'alpha', name: 'Alpha', permissions: {}, membership: { role: 'owner', status: 'active' } };
const row = (id, title, url = '') => ({ id, display_name: title, file_type: 'video', song_summary: { id: id + 1000, title, artist: 'Artist' }, playback: { video_url: url }, can_feature: false });
const page = (media, more = false) => ({ media, pagination: { has_more: more, next_offset: more ? 25 : null } });
const load = async () => {
  if (vite) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  shell = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx');
  featured = await vite.ssrLoadModule('/src/components/workspace/featured-media/FeaturedMediaWorkspace.jsx');
  context = await vite.ssrLoadModule('/src/components/workspace/WorkspaceContext.jsx');
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
  upload = await vite.ssrLoadModule('/src/context/UploadContext.jsx');
};
afterEach(() => cleanup());
after(async () => { await vite?.close(); dom.window.close(); });
const Providers = ({ children, account = authValue, revision = 0 }) => h(auth.AuthContext.Provider, { value: account },
  h(bands.BandContext.Provider, { value: { activeBands: [], membershipRevisions: { alpha: revision } } },
    h(upload.UploadContext.Provider, { value: { watch: () => () => {}, retryAudio: async () => {} } }, children)));
const Controls = () => { const navigate = useNavigate(); return h('button', { onClick: () => navigate('/bands/beta/representative-media') }, 'select band'); };
const App = ({ account, revision }) => h(Providers, { account, revision },
  h(MemoryRouter, { initialEntries: ['/bands/alpha/representative-media'] }, h(Controls),
    h(Routes, null, h(Route, { path: '/bands/:bandSlug', element: h(shell.BandWorkspaceRoute) },
      h(Route, { path: 'representative-media', element: h(featured.RepresentativeMediaWorkspace) })))));

for (const boundary of ['band', 'account', 'membership']) {
  test(`featured workspace drops pending pages and open playback after ${boundary} changes`, async () => {
    await load(); const previousFetch = globalThis.fetch; const pendingMedia = deferred(); const pendingSongs = deferred(); const signals = []; let changed = false;
    globalThis.fetch = async (url, options = {}) => {
      const path = String(url);
      if (path.includes('/by-slug/')) return response({ band: path.includes('/beta') ? { ...band, id: 42, slug: 'beta' } : band });
      if (path.includes('/featured-media?')) {
        if (path.includes('offset=25') && !changed) { signals.push(options.signal); return pendingMedia.promise; }
        return response(page([row(changed ? 801 : 701, changed ? 'Fresh feature' : 'Old feature', 'https://media.invalid/first.mp4')], !changed));
      }
      if (path.includes('/songs?')) {
        if (path.includes('offset=20') && !changed) { signals.push(options.signal); return pendingSongs.promise; }
        return response({ songs: [{ id: changed ? 802 : 702, title: changed ? 'Fresh song' : 'Old song', artist: 'Artist', can_feature: true }], pagination: { has_more: !changed, next_offset: 20 } });
      }
      throw new Error(`Unexpected request: ${path}`);
    };
    try {
      const view = render(h(App, {})); await view.findByRole('heading', { name: 'Old feature' });
      fireEvent.click(view.getByRole('button', { name: '재생', exact: true }));
      await waitFor(() => assert.ok(view.container.querySelector('video')));
      fireEvent.click(view.getByRole('button', { name: '대표 미디어 더 불러오기' }));
      fireEvent.click(view.getByRole('button', { name: '곡 더 불러오기' }));
      await waitFor(() => assert.equal(signals.length, 2)); changed = true;
      if (boundary === 'band') fireEvent.click(view.getByRole('button', { name: 'select band' }));
      else view.rerender(h(App, boundary === 'account' ? { account: { ...authValue, user: { id: 'account-b' } } } : { revision: 1 }));
      await view.findByRole('heading', { name: 'Fresh feature' }); assert.ok(signals.every((signal) => signal.aborted));
      assert.equal(view.container.querySelector('video'), null);
      assert.equal(view.getByRole('combobox', { name: '곡 선택' }).value, '');
      await act(async () => { pendingMedia.resolve(response(page([row(999, 'Obsolete feature')]))); pendingSongs.resolve(response({ songs: [{ id: 999, title: 'Obsolete song', artist: 'Artist', can_feature: true }], pagination: { has_more: false } })); });
      assert.equal(view.queryByRole('heading', { name: 'Obsolete feature' }), null); assert.equal(view.queryByRole('option', { name: /Obsolete song/ }), null);
      assert.equal(view.queryByRole('heading', { name: 'Old feature' }), null);
    } finally { globalThis.fetch = previousFetch; }
  });
}

test('featured latest request wins across pagination and URL refresh, preserving offset merge and aborting unmount', async () => {
  await load(); const obsoletePage = deferred(); const unmountedPage = deferred(); const calls = []; let page25Reads = 0;
  const api = { requestJson: async (path, options = {}) => {
    if (path.includes('/songs?')) return { songs: [], pagination: { has_more: false } };
    const offset = new URL(path, 'http://localhost').searchParams.get('offset'); calls.push({ offset, signal: options.signal });
    if (offset === '25') {
      page25Reads += 1;
      if (page25Reads === 1) return obsoletePage.promise;
      if (page25Reads === 4) return unmountedPage.promise;
      return page([row(701, 'First refreshed', 'https://media.invalid/first-new.mp4'), row(702, 'Second', `https://media.invalid/second-${page25Reads}.mp4`)]);
    }
    return page([row(701, calls.length > 1 ? 'First refreshed' : 'First', 'https://media.invalid/first-new.mp4')], true);
  } };
  const app = h(Providers, null, h(context.WorkspaceContext.Provider, { value: { band, api, workspaceKey: 'alpha:a' } }, h(MemoryRouter, null, h(featured.RepresentativeMediaWorkspace))));
  const view = render(app); await view.findByRole('heading', { name: 'First' });
  fireEvent.click(view.getByRole('button', { name: '재생', exact: true }));
  fireEvent.click(view.getByRole('button', { name: '대표 미디어 더 불러오기' })); await waitFor(() => assert.equal(calls.length, 2));
  // The player error remains active while the load-more button is disabled.
  fireEvent.error(view.container.querySelector('video'));
  await view.findByRole('heading', { name: 'First refreshed' }); assert.equal(calls[1].signal.aborted, true);
  await act(async () => obsoletePage.resolve(page([row(999, 'Obsolete page')])));
  assert.equal(view.queryByRole('heading', { name: 'Obsolete page' }), null);
  fireEvent.click(view.getByRole('button', { name: '대표 미디어 더 불러오기' })); await view.findByRole('heading', { name: 'Second' });
  assert.equal(view.container.querySelectorAll('.representative-media-card').length, 2);
  const second = view.getByRole('heading', { name: 'Second' }).closest('article');
  fireEvent.click(second.querySelector('.primary-btn'));
  fireEvent.error(second.querySelector('video'));
  await waitFor(() => assert.equal(second.querySelector('video')?.getAttribute('src'), 'https://media.invalid/second-3.mp4'));
  assert.equal(calls.at(-1).offset, '25'); assert.ok(view.getByRole('heading', { name: 'First refreshed' }));
  fireEvent.error(second.querySelector('video')); await waitFor(() => assert.equal(page25Reads, 4));
  const pendingSignal = calls.at(-1).signal; view.unmount(); assert.equal(pendingSignal.aborted, true);
  await act(async () => unmountedPage.resolve(page([row(999, 'Unmounted page')]))); assert.equal(view.container.textContent, '');
});
