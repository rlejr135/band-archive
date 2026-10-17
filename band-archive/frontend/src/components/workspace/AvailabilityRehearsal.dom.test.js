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
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
let vite; let workspace; let auth; let bands;
const day = '2026-10-17';
const initialFingerprint = JSON.stringify([0, [12], [[12, 1]]]);
const load = async () => { if (workspace) return; vite = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); workspace = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); bands = await vite.ssrLoadModule('/src/context/BandContext.jsx'); };
const response = (payload, status = 200) => ({ ok: status < 400, status, json: async () => payload });
const app = () => createElement(auth.AuthContext.Provider, { value: { user: { id: 'user' }, getAccessToken: async () => 'test', refreshSession: async () => 'test', signOut: async () => {} } }, createElement(bands.BandContext.Provider, { value: { activeBands: [] } }, createElement(MemoryRouter, { initialEntries: [`/bands/one/rehearsals?${new URLSearchParams({ create: '1', date: day, period: 'evening', availability: initialFingerprint })}`] }, createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) }, createElement(Route, { path: 'rehearsals', element: createElement(workspace.RehearsalWorkspace) }))))));
const installFetch = ({ changed = false, failPost = false } = {}) => {
  const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); calls.push({ path, options });
    if (path.includes('/by-slug/')) return response({ band: { id: 8, slug: 'one', name: '밴드', membership: { role: 'owner', status: 'active' } } });
    if (path.includes('/availability?')) return response({ band_id: 8, generation: changed ? 1 : 0, members: [{ membership_id: 12 }], days: [{ membership_id: 12, date: day, revision: 1, morning: 'UNKNOWN', afternoon: 'UNKNOWN', evening: 'AVAILABLE' }] });
    if (options.method === 'POST' && path.includes('/rehearsals')) { if (failPost) throw new TypeError('Connection lost'); return response({ rehearsal: { id: 99 } }); }
    if (path.includes('/songs?')) return response({ songs: [] });
    return response({ rehearsals: [], pagination: { has_more: false } });
  };
  return { calls, restore: () => { globalThis.fetch = previous; } };
};
afterEach(() => cleanup()); after(async () => { await vite?.close(); dom.window.close(); });
test('candidate date prefills but exact time stays blank; changed availability needs reconfirmation', async () => {
  await load(); const mock = installFetch({ changed: true }); const previousConfirm = window.confirm; let confirms = 0; window.confirm = () => { confirms += 1; return false; };
  try {
    const view = render(app()); const title = await view.findByLabelText('제목'); assert.equal(view.getByLabelText('날짜').value, day); assert.equal(view.getByLabelText('시간').value, '');
    await userEvent.type(title, '다음 합주'); fireEvent.submit(title.closest('form'));
    await waitFor(() => assert.equal(confirms, 1)); assert.equal(mock.calls.filter(({ options }) => options.method === 'POST').length, 0);
    assert.ok(view.getByText(/정확한 합주 시각은 직접 입력/));
  } finally { mock.restore(); window.confirm = previousConfirm; }
});
test('unknown candidate creation result blocks blind retry rather than making duplicate rehearsals', async () => {
  await load(); const mock = installFetch({ failPost: true });
  try {
    const view = render(app()); const title = await view.findByLabelText('제목'); await userEvent.type(title, '다음 합주'); fireEvent.submit(title.closest('form'));
    await view.findByText(/중복 생성을 막기 위해 다시 저장하지 않습니다/); const button = view.getByRole('button', { name: '저장', exact: true }); assert.equal(button.disabled, true);
    await act(async () => { fireEvent.submit(title.closest('form')); }); assert.equal(mock.calls.filter(({ options }) => options.method === 'POST').length, 1);
    assert.equal(view.getByRole('button', { name: '취소', exact: true }).disabled, false);
  } finally { mock.restore(); }
});
