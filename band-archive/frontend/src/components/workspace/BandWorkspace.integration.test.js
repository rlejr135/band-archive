import assert from 'node:assert/strict';
/* eslint-disable react-hooks/globals */
import test, { after } from 'node:test';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { createServer } from 'vite';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let vite;
let workspace;
let auth;
let bands;

const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

const load = async () => {
  if (workspace) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  workspace = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx');
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
};
after(async () => { await vite?.close(); });

test('real route remount shows no A workspace while B is unresolved and rejects a late A response', async () => {
  await load();
  const oldFetch = globalThis.fetch;
  const a = deferred(); const b = deferred(); let navigate;
  globalThis.fetch = async (url) => url.endsWith('/by-slug/band-a') ? a.promise : b.promise;
  const Capture = () => { navigate = useNavigate(); return null; };
  const authValue = { user: { id: 'account-a' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  const bandValue = { activeBands: [], selectedBand: null };
  let renderer;
  await act(async () => { renderer = create(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: bandValue }, createElement(MemoryRouter, { initialEntries: ['/bands/band-a'] }, createElement(Capture), createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) })))))); await flush(); });
  await act(async () => { a.resolve(response({ band: { id: 1, slug: 'band-a', name: 'A private', membership: { id: 1 } } })); await flush(); });
  assert.match(JSON.stringify(renderer.toJSON()), /A private/);
  await act(async () => { navigate('/bands/band-b'); await flush(); });
  const immediate = JSON.stringify(renderer.toJSON());
  assert.doesNotMatch(immediate, /A private/);
  assert.match(immediate, /밴드 공간을 여는 중/);
  await act(async () => { a.resolve(response({ band: { id: 1, slug: 'band-a', name: 'A private' } })); b.resolve(response({ band: { id: 2, slug: 'band-b', name: 'B public' } })); await flush(); });
  assert.match(JSON.stringify(renderer.toJSON()), /B public/);
  assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /A private/);
  globalThis.fetch = oldFetch;
});

test('member route exposes create UI and submits a tenant-free v2 payload while visitor stays read-only', async () => {
  await load();
  const oldFetch = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/member-band')) return response({ band: { id: 7, slug: 'member-band', name: 'Member band', default_visibility: 'MEMBERS', membership: { id: 70, role: 'member' } } });
    if (url.includes('/by-slug/public-band')) return response({ band: { id: 8, slug: 'public-band', name: 'Public band', default_visibility: 'PUBLIC', membership: null } });
    if (url.includes('/songs') && options.method === 'POST') { writes.push(JSON.parse(options.body)); return response({ song: { id: 1 } }, 201); }
    if (url.includes('/songs?')) return response({ songs: [], pagination: { total: 0, has_more: false } });
    return response({ rehearsals: [], pagination: { total: 0, has_more: false } });
  };
  const authValue = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  const bandValue = { activeBands: [], selectedBand: null };
  const tree = (entry) => createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: bandValue }, createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) }, createElement(Route, { path: 'songs', element: createElement(workspace.SongWorkspace) }))))));
  let renderer;
  await act(async () => { renderer = create(tree('/bands/member-band/songs')); await flush(); });
  const add = renderer.root.findAllByType('button').find((button) => button.children.join('') === '곡 추가');
  assert.ok(add);
  await act(async () => { add.props.onClick(); });
  const inputs = renderer.root.findAllByType('input');
  await act(async () => { inputs.find((input) => input.props.name === 'title').props.onChange({ target: { name: 'title', value: '새 곡' } }); inputs.find((input) => input.props.name === 'artist').props.onChange({ target: { name: 'artist', value: '밴드' } }); });
  await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); await new Promise((resolve) => setTimeout(resolve, 0)); await flush(); });
  assert.deepEqual(writes[0], { title: '새 곡', artist: '밴드', status: 'Practice', chords: '', link: '', memo: '', genre: '', visibility: 'MEMBERS' });
  await act(async () => { renderer.unmount(); renderer = create(tree('/bands/public-band/songs')); await flush(); });
  assert.equal(renderer.root.findAllByType('button').some((button) => button.children.join('') === '곡 추가'), false);
  assert.match(JSON.stringify(renderer.toJSON()), /멤버가 되면 곡과 합주를 작성/);
  globalThis.fetch = oldFetch;
});
