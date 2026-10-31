import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { createElement as h, StrictMode } from 'react';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};
let vite; let context; let auth; let song; let rehearsal;
const load = async () => {
  if (vite) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  context = await vite.ssrLoadModule('/src/components/workspace/WorkspaceContext.jsx');
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  song = (await vite.ssrLoadModule('/src/components/workspace/songs/SongDetail.jsx')).SongDetail;
  rehearsal = (await vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalDetail.jsx')).RehearsalDetail;
};
afterEach(() => cleanup());
after(async () => { await vite?.close(); dom.window.close(); });

const deferred = () => {
  let resolve; let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};
const band = { id: 41, slug: 'alpha', name: 'Alpha', membership: { role: 'member', status: 'active' } };
const account = { user: { id: 'account-a' }, getAccessToken: async () => 'synthetic-token', refreshSession: async () => 'synthetic-token' };
const item = (id) => ({ id, title: `Resource ${id}`, artist: 'Artist', date: '2026-10-31', songs: [], can_delete: false });
const media = (id, name, extra = {}) => ({ id, display_name: name, file_type: 'video', playback: { video_url: `https://media.invalid/${id}.mp4` }, can_delete: false, ...extra });
const page = (rows, more = false) => ({ media: rows, pagination: { has_more: more, next_offset: more ? 20 : null } });
const makeApi = (read, mutate = async () => ({})) => {
  const calls = [];
  const api = { requestJson: async (path, options = {}) => {
    if (options.method) return mutate(path, options);
    const url = new URL(path, 'http://localhost');
    const match = url.pathname.match(/\/bands\/(\d+)\/(songs|rehearsals)\/(\d+)\/media$/);
    if (!match) {
      assert.match(path, /\/media\/\d+\/comments\?/);
      return { comments: [], pagination: { has_more: false } };
    }
    const call = { bandId: Number(match[1]), resourceId: Number(match[3]), offset: Number(url.searchParams.get('offset')), signal: options.signal, options };
    calls.push(call);
    return read(call, calls);
  } };
  return { api, calls };
};
const detail = (kind, api, id = 1, value = {}, strict = false) => {
  const child = h(auth.AuthContext.Provider, { value: account },
    h(context.WorkspaceContext.Provider, { value: { band, api, workspaceKey: 'alpha:account-a', ...value } },
      h(kind === 'song' ? song : rehearsal, { item: item(id), onEdit: () => {}, onDeleted: () => {} })));
  return strict ? h(StrictMode, null, child) : child;
};
const visible = (view, label) => view.queryByText(label, { exact: true }) !== null;
const more = (view) => fireEvent.click(view.getByRole('button', { name: '미디어 더 불러오기', exact: true }));

for (const kind of ['song', 'rehearsal']) {
  for (const phase of ['while replacement is pending', 'after replacement resolves']) {
    test(`${kind} detail clears previous resource pages ${phase}`, async () => {
      await load(); const next = deferred();
      const { api, calls } = makeApi(({ resourceId, offset }) => resourceId === 2 ? next.promise : page([media(offset ? 12 : 11, offset ? 'Previous later page' : 'Previous first page')], !offset));
      const view = render(detail(kind, api));
      await view.findByText('Previous first page', { exact: true }); more(view);
      await view.findByText('Previous later page', { exact: true });
      view.rerender(detail(kind, api, 2));
      await waitFor(() => assert.equal(calls.at(-1).resourceId, 2));
      if (phase === 'after replacement resolves') {
        await act(async () => next.resolve(page([media(21, 'Current resource')])));
        await view.findByText('Current resource', { exact: true });
      }
      assert.equal(visible(view, 'Previous first page'), false);
      assert.equal(visible(view, 'Previous later page'), false);
      if (phase === 'while replacement is pending') await act(async () => next.resolve(page([media(21, 'Current resource')])));
    });
  }

  test(`${kind} detail ignores aborted old resource pages even when transport resolves them`, async () => {
    await load(); const old = deferred();
    const { api, calls } = makeApi(({ resourceId, offset }) => offset ? old.promise : page([media(resourceId * 10, resourceId === 1 ? 'Old resource' : 'New resource')], resourceId === 1));
    const view = render(detail(kind, api)); await view.findByText('Old resource', { exact: true }); more(view);
    await waitFor(() => assert.equal(calls.length, 2)); const signal = calls[1].signal;
    view.rerender(detail(kind, api, 2)); await view.findByText('New resource', { exact: true });
    assert.equal(signal.aborted, true);
    await act(async () => old.resolve(page([media(99, 'Late obsolete page')])));
    assert.equal(visible(view, 'Late obsolete page'), false);
    assert.equal(visible(view, 'New resource'), true);
    assert.equal(calls[0].options.authenticated, false); assert.equal(calls[0].options.optionalAuth, true);
  });

  test(`${kind} detail resets media and pending requests for another workspace with the same numeric resource id`, async () => {
    await load(); const next = deferred();
    const { api, calls } = makeApi(({ bandId }) => bandId === 41 ? page([media(11, 'Original workspace media')]) : next.promise);
    const view = render(detail(kind, api)); await view.findByText('Original workspace media', { exact: true });
    view.rerender(detail(kind, api, 1, { band: { ...band, id: 42, slug: 'beta' }, workspaceKey: 'beta:account-b' }));
    await waitFor(() => assert.equal(calls.at(-1).bandId, 42));
    assert.equal(visible(view, 'Original workspace media'), false);
    await act(async () => next.resolve(page([media(21, 'Other workspace media')])));
    await view.findByText('Other workspace media', { exact: true });
    assert.equal(visible(view, 'Original workspace media'), false);
  });

  test(`${kind} detail permits one stale refresh per page and media, resetting when revisiting a resource`, async () => {
    await load();
    const { api, calls } = makeApi(({ resourceId }) => page([media(resourceId * 10, `Media ${resourceId}`)]));
    const view = render(detail(kind, api)); await view.findByText('Media 1', { exact: true });
    fireEvent.error(view.container.querySelector('video'));
    await waitFor(() => assert.equal(calls.length, 2));
    await waitFor(() => assert.equal(view.queryByText('미디어를 불러오는 중…') === null, true));
    fireEvent.error(view.container.querySelector('video'));
    await act(async () => {}); assert.equal(calls.length, 2);
    view.rerender(detail(kind, api, 2)); await view.findByText('Media 2', { exact: true });
    view.rerender(detail(kind, api, 1)); await view.findByText('Media 1', { exact: true });
    fireEvent.error(view.container.querySelector('video'));
    await waitFor(() => assert.equal(calls.length, 5));
    assert.equal(calls.at(-1).offset, 0);
  });

  test(`${kind} detail authoritative vote refresh replaces pages and retains server ordering`, async () => {
    await load(); let refreshed = false;
    const { api, calls } = makeApi(({ offset }) => refreshed ? page([media(12, 'New server first', { vote_score: 7 })]) : page([media(offset ? 12 : 11, offset ? 'Second page' : 'First page')], !offset),
      async (path, options) => { assert.match(path, /\/media\/11\/vote$/); assert.equal(options.method, 'PUT'); refreshed = true; return { viewer_vote: 1, media: media(11, 'Updated before authoritative refresh', { vote_score: 1 }) }; });
    const view = render(detail(kind, api)); await view.findByText('First page', { exact: true }); more(view);
    await view.findByText('Second page', { exact: true });
    fireEvent.click(view.getAllByRole('button', { name: '추천', exact: true })[0]);
    await view.findByText('New server first', { exact: true });
    assert.equal(view.container.querySelectorAll('.workspace-media').length, 1);
    assert.equal(visible(view, 'First page'), false); assert.equal(visible(view, 'Second page'), false);
    assert.equal(calls.at(-1).offset, 0);
    assert.equal(view.queryByRole('button', { name: '미디어 더 불러오기', exact: true }) === null, true);
  });

  test(`${kind} detail latest pagination request wins and aborts outstanding work on unmount`, async () => {
    await load(); const obsolete = deferred(); const unmounted = deferred(); let laterReads = 0;
    const { api, calls } = makeApi(({ offset }) => {
      if (!offset) return page([media(11, 'Initial page')], true);
      laterReads += 1;
      if (laterReads === 1) return obsolete.promise;
      if (laterReads === 3) return unmounted.promise;
      return page([media(12, 'Newest page')], true);
    });
    const view = render(detail(kind, api)); await view.findByText('Initial page', { exact: true }); more(view);
    await waitFor(() => assert.equal(calls.length, 2));
    // A stale URL refresh remains available while load-more is in flight.
    fireEvent.error(view.container.querySelector('video'));
    await waitFor(() => assert.equal(calls.length, 3));
    await waitFor(() => assert.equal(view.getByRole('button', { name: '미디어 더 불러오기', exact: true }).disabled, false));
    more(view); await view.findByText('Newest page', { exact: true });
    await act(async () => obsolete.resolve(page([media(99, 'Obsolete request')])));
    assert.equal(visible(view, 'Obsolete request'), false);
    more(view); await waitFor(() => assert.equal(laterReads, 3)); const signal = calls.at(-1).signal;
    view.unmount(); assert.equal(signal.aborted, true);
    await act(async () => unmounted.resolve(page([media(98, 'Unmounted request')])));
    assert.equal(view.container.textContent, '');
  });

  test(`${kind} detail StrictMode replay aborts its first request and ignores its late response`, async () => {
    await load(); const abandoned = deferred();
    const { api, calls } = makeApi((_, reads) => reads.length === 1 ? abandoned.promise : page([media(11, 'Strict current media')]));
    const view = render(detail(kind, api, 1, {}, true));
    await view.findByText('Strict current media', { exact: true });
    assert.equal(calls.length, 2); assert.equal(calls[0].signal.aborted, true); assert.equal(calls[1].signal.aborted, false);
    await act(async () => abandoned.resolve(page([media(99, 'Strict abandoned media')])));
    assert.equal(visible(view, 'Strict abandoned media'), false);
  });
}
