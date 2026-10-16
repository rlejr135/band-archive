import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
let vite; let auth; let DeleteAction;
const load = async () => { if (DeleteAction) return; vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); DeleteAction = (await vite.ssrLoadModule('/src/components/workspace/BandDeleteAction.jsx')).BandDeleteAction; };
after(async () => { await vite?.close(); dom.window.close(); }); afterEach(() => cleanup());

test('owner delete action follows the trusted task id and refreshes only after async completion', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; const calls = []; let completed = 0; window.confirm = () => true;
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); const method = options.method || 'GET'; calls.push({ path, method });
    if (path.endsWith('/api/v2/bands/44/gallery/71') && method === 'DELETE') return response({ task_id: 'task-71', status: 'queued', retry_after_seconds: 0 });
    if (path.endsWith('/api/v2/bands/44/deletion-tasks/task-71')) return response({ status: 'completed', task_id: 'task-71', resource_type: 'gallery', resource_id: 71 });
    throw new Error(`unexpected ${method} ${path}`);
  };
  try {
    const authValue = { user: { id: 'owner' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(DeleteAction, { band: { id: 44 }, workspaceKey: '44:owner', canDelete: true, label: '갤러리 사진', path: '/api/v2/bands/44/gallery/71', asynchronous: true, resourceType: 'gallery', onDeleted: () => { completed += 1; } })));
    await userEvent.click(view.getByRole('button', { name: '삭제' }));
    await waitFor(() => assert.equal(completed, 1), { timeout: 3000 });
    assert.deepEqual(calls.map(({ path, method }) => ({ path: path.replace(/^https?:\/\/[^/]+/, ''), method })), [
      { path: '/api/v2/bands/44/gallery/71', method: 'DELETE' },
      { path: '/api/v2/bands/44/deletion-tasks/task-71', method: 'GET' },
    ]);
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('delete action hides without the server can_delete flag and stops polling when unmounted', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; const calls = []; window.confirm = () => true;
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ task_id: 'stale', status: 'queued', retry_after_seconds: 3 }); };
  try {
    const authValue = { user: { id: 'owner' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
    const denied = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(DeleteAction, { band: { id: 45 }, canDelete: false, label: '미디어', path: '/api/v2/bands/45/media/1' })));
    assert.equal(denied.queryByRole('button', { name: '삭제' }), null); denied.unmount();
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(DeleteAction, { band: { id: 45 }, workspaceKey: '45:owner', canDelete: true, label: '미디어', path: '/api/v2/bands/45/media/1', asynchronous: true })));
    await userEvent.click(view.getByRole('button', { name: '삭제' }));
    await view.findByText(/삭제 작업을 안전하게 처리/);
    view.unmount();
    await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
    assert.deepEqual(calls.map((path) => path.replace(/^https?:\/\/[^/]+/, '')), ['/api/v2/bands/45/media/1']);
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('delete action cancels its scoped poll when the server can_delete capability is revoked', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; const calls = []; window.confirm = () => true;
  globalThis.fetch = async (url, options = {}) => { calls.push({ path: String(url), method: options.method || 'GET' }); return response({ task_id: 'revoked-task', status: 'queued', retry_after_seconds: 3 }); };
  try {
    const authValue = { user: { id: 'owner' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
    const props = (canDelete) => createElement(auth.AuthContext.Provider, { value: authValue }, createElement(DeleteAction, { band: { id: 46 }, workspaceKey: '46:owner', canDelete, label: '미디어', path: '/api/v2/bands/46/media/2', asynchronous: true, resourceType: 'media' }));
    const view = render(props(true));
    await userEvent.click(view.getByRole('button', { name: '삭제' }));
    await view.findByText(/삭제 작업을 안전하게 처리/);
    view.rerender(props(false));
    assert.equal(view.queryByRole('button', { name: '삭제' }), null);
    await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
    assert.deepEqual(calls.map(({ path, method }) => ({ path: path.replace(/^https?:\/\/[^/]+/, ''), method })), [{ path: '/api/v2/bands/46/media/2', method: 'DELETE' }]);
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});
