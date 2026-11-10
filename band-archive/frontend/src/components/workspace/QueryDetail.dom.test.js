import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { createElement as h } from 'react';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
let vite; let WorkspaceContext; let useQueryDetail;
const load = async () => {
  if (vite) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  ({ WorkspaceContext } = await vite.ssrLoadModule('/src/components/workspace/WorkspaceContext.jsx'));
  ({ useQueryDetail } = await vite.ssrLoadModule('/src/components/workspace/useQueryDetail.js'));
};
afterEach(cleanup);
after(async () => { await vite?.close(); dom.window.close(); });
const extract = (response) => response.song;
const pathForId = (id) => `/api/v2/bands/4/songs/${id}`;
const Probe = (props) => {
  const state = useQueryDetail({ ...props, pathForId, extract });
  return h('output', { 'data-testid': 'state' }, JSON.stringify(state));
};
const tree = (api, detailValue, identity = 'band4:accountA', bandId = 4) => h(WorkspaceContext.Provider,
  { value: { api, band: { id: bandId } } }, h(Probe, { detailValue, identity }));
const state = (view) => JSON.parse(view.getByTestId('state').textContent);
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

test('query detail does no read for absent/invalid IDs and validates response resource and band', async () => {
  await load(); const calls = [];
  let payload = { song: { id: 3, band_id: 4, title: 'Correct' } };
  const api = { requestJson: async (path) => { calls.push(path); return payload; } };
  const view = render(tree(api, null));
  assert.deepEqual(state(view), { id: null, loading: false, error: '', item: null });
  for (const invalid of ['', '0', '-1', '1.5', '03', 'word', '9007199254740992']) {
    view.rerender(tree(api, invalid));
    assert.equal(Boolean(state(view).error), true);
  }
  assert.equal(calls.length, 0);
  view.rerender(tree(api, '3'));
  await waitFor(() => assert.equal(state(view).item?.title, 'Correct'));
  assert.equal(calls.length, 1);
  for (const item of [{ id: 5, band_id: 4 }, { id: 4, band_id: 99 }, null]) {
    payload = { song: item };
    view.rerender(tree(api, '4', `case:${JSON.stringify(item)}`));
    await waitFor(() => assert.equal(Boolean(state(view).error), true));
    assert.equal(state(view).item, null);
  }
  payload = { song: { id: 4, title: 'DTO without band_id' } };
  view.rerender(tree(api, '4', 'compatibility'));
  await waitFor(() => assert.equal(state(view).item?.title, 'DTO without band_id'));
});

test('query detail aborts replaced/unmounted reads and ignores reverse responses across account and band', async () => {
  await load(); const calls = [];
  const api = { requestJson: (path, options) => { const call = { path, ...options, ...deferred() }; calls.push(call); return call.promise; } };
  const view = render(tree(api, '3'));
  view.rerender(tree(api, '4'));
  assert.equal(calls[0].signal.aborted, true);
  await act(async () => { calls[1].resolve({ song: { id: 4, band_id: 4, title: 'Latest' } }); });
  assert.equal(state(view).item.title, 'Latest');
  await act(async () => { calls[0].resolve({ song: { id: 3, band_id: 4, title: 'Stale' } }); });
  assert.equal(state(view).item.title, 'Latest');
  view.rerender(tree(api, '4', 'band4:accountB'));
  assert.equal(state(view).item, null);
  assert.equal(state(view).loading, true);
  view.rerender(tree(api, '4', 'band5:accountB', 5));
  assert.equal(calls[2].signal.aborted, true);
  await act(async () => { calls[2].resolve({ song: { id: 4, band_id: 4, title: 'Old account' } }); });
  assert.equal(state(view).item, null);
  view.unmount();
  assert.equal(calls[3].signal.aborted, true);
  await act(async () => { calls[3].resolve({ song: { id: 4, band_id: 5, title: 'Unmounted' } }); });
});

test('query detail reports request failure and clears query state without another read', async () => {
  await load(); let count = 0;
  const api = { requestJson: async () => { count += 1; throw new Error('unavailable'); } };
  const view = render(tree(api, '7'));
  await waitFor(() => assert.equal(Boolean(state(view).error), true));
  view.rerender(tree(api, null));
  assert.equal(state(view).error, '');
  assert.equal(count, 1);
});
