import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
// React's legacy input-event fallback probes these IE methods in jsdom.
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};

let vite; let workspace; let auth; let bands;
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const load = async () => { if (workspace) return; vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); workspace = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); bands = await vite.ssrLoadModule('/src/context/BandContext.jsx'); };
after(async () => { await vite?.close(); dom.window.close(); });

const App = ({ entry, authValue, bandValue, songs = false }) => createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: bandValue }, createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) }, songs ? createElement(Route, { path: 'songs', element: createElement(workspace.SongWorkspace) }) : null)))));

test('DOM route navigation immediately removes A and ignores its late response', async () => {
  await load(); const previousFetch = globalThis.fetch; const a = deferred(); const b = deferred(); let navigate;
  globalThis.fetch = async (url) => url.endsWith('/by-slug/band-a') ? a.promise : b.promise;
  const Go = () => { navigate = useNavigate(); return null; };
  const authValue = { user: { id: 'account-a' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: { activeBands: [] } }, createElement(MemoryRouter, { initialEntries: ['/bands/band-a'] }, createElement(Go), createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) }))))));
  await act(async () => { a.resolve(response({ band: { id: 1, slug: 'band-a', name: 'A secret' } })); }); await view.findByText('A secret');
  await act(async () => { navigate('/bands/band-b'); });
  assert.equal(view.queryByText('A secret'), null);
  await act(async () => { b.resolve(response({ band: { id: 2, slug: 'band-b', name: 'B public' } })); }); await view.findByText('B public');
  assert.equal(view.queryByText('A secret'), null); view.unmount(); globalThis.fetch = previousFetch;
});

test('member DOM form writes tenant-free v2 payload once; visitor sees no write control', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => { if (url.includes('/by-slug/member')) return response({ band: { id: 7, slug: 'member', name: 'Member', default_visibility: 'MEMBERS', membership: { id: 7, role: 'member' } } }); if (url.includes('/songs') && options.method === 'POST') { writes.push(JSON.parse(options.body)); return response({ song: {} }, 201); } if (url.includes('/songs?')) return response({ songs: [], pagination: { total: 0, has_more: false } }); return response({}); };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  const view = render(createElement(App, { entry: '/bands/member/songs', authValue: member, bandValue: { activeBands: [] }, songs: true }));
  await view.findByRole('button', { name: '곡 추가' }); await userEvent.click(view.getByRole('button', { name: '곡 추가' }));
  await userEvent.type(view.getByLabelText('제목'), '새 곡'); await userEvent.type(view.getByLabelText('아티스트'), '밴드');
  await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.equal(writes.length, 1));
  assert.equal('band_id' in writes[0], false); assert.equal('created_by_membership_id' in writes[0], false); assert.equal(writes[0].title, '새 곡');
  view.unmount(); globalThis.fetch = async (url) => url.includes('/by-slug/public') ? response({ band: { id: 8, slug: 'public', name: 'Public', default_visibility: 'PUBLIC', membership: null } }) : response({ songs: [], pagination: { total: 0, has_more: false } });
  const visitor = render(createElement(App, { entry: '/bands/public/songs', authValue: member, bandValue: { activeBands: [] }, songs: true })); await visitor.findByText('Public'); assert.equal(visitor.queryByRole('button', { name: '곡 추가' }), null); visitor.unmount(); globalThis.fetch = previousFetch;
});
