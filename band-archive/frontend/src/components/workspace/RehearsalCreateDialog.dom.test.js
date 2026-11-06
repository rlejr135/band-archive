import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
const { createElement: h } = await import('react');
const { act, cleanup, fireEvent, render, waitFor, within } = await import('@testing-library/react');
const { default: userEvent } = await import('@testing-library/user-event');
const { MemoryRouter } = await import('react-router-dom');
let vite; let Workspace;
const load = async () => {
  if (vite) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true }, plugins: [{ name: 'rehearsal-create-dialog-doubles', enforce: 'pre', transform(_code, id) {
    const path = id.replaceAll('\\', '/');
    if (path.endsWith('/workspace/WorkspaceContext.jsx')) return 'export const useWorkspace = () => globalThis.__rehearsalDialogWorkspace;';
    if (path.endsWith('/workspace/AvailabilityCalendar.jsx')) return 'export const RehearsalTabs = () => null;';
    if (path.endsWith('/rehearsals/RehearsalDetail.jsx')) return `import React from 'react'; export const RehearsalDetail = ({item}) => React.createElement('p', null, '상세 '+item.title);`;
    if (path.endsWith('/context/ServicePolicyContext.jsx')) return 'export const useServicePolicy = () => ({config:{policies:{upload:{enabled:false}}}});';
  } }] });
  Workspace = (await vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalWorkspace.jsx')).RehearsalWorkspace;
};
afterEach(() => { cleanup(); document.body.style.overflow = ''; });
after(async () => { await vite?.close(); dom.window.close(); delete globalThis.__rehearsalDialogWorkspace; });
const setup = ({ mobile = true, list = 'empty', post = async () => ({ rehearsal: { id: 9 } }) } = {}) => {
  const listeners = new Set();
  const media = { matches: mobile, addEventListener: (_event, fn) => listeners.add(fn), removeEventListener: (_event, fn) => listeners.delete(fn) };
  window.matchMedia = () => media;
  const calls = [];
  const api = { requestJson: (path, options = {}) => {
    calls.push({ path, options });
    if (options.method === 'POST') return post(path, options);
    if (path.includes('/songs?')) return Promise.resolve({ songs: [], pagination: { has_more: false } });
    if (path.includes('/availability?')) return Promise.resolve({ band_id: 1, generation: 0, members: [], days: [] });
    if (list === 'loading') return new Promise(() => {});
    if (list === 'error') return Promise.reject(new Error('list unavailable'));
    return Promise.resolve({ rehearsals: list === 'nonempty' ? [{ id: 4, title: '기존 합주', date: '2026-11-06' }] : [], pagination: { has_more: false } });
  } };
  globalThis.__rehearsalDialogWorkspace = { band: { id: 1, slug: 'one', default_visibility: 'MEMBERS', membership: { role: 'owner' } }, workspaceKey: 'one:user', api };
  return { calls, resize: async (matches) => { await act(async () => { media.matches = matches; for (const fn of listeners) fn(); }); } };
};
const app = (query = '') => h(MemoryRouter, { initialEntries: [`/bands/one/rehearsals${query}`] }, h(Workspace));
const fill = async (view) => { const user = userEvent.setup({ document: dom.window.document }); await user.type(view.getByLabelText('제목'), '새 합주 초안'); fireEvent.change(view.getByLabelText('날짜'), { target: { value: '2026-11-07' } }); return user; };

for (const list of ['loading', 'error']) test(`mobile direct create renders one dialog independently of list ${list}`, async () => {
  await load(); setup({ list }); const view = render(app('?create=1'));
  const dialog = await view.findByRole('dialog', { name: '새 합주' });
  assert.equal(dialog.contains(view.getByLabelText('제목')), true);
  assert.equal(view.getAllByLabelText('제목').length, 1);
  fireEvent.click(within(dialog).getByRole('button', { name: '합주 추가 닫기' }));
  assert.equal(view.queryByRole('dialog') === null, true);
});

test('header and empty-list create share a dialog with focus, scroll lock and explicit discard', async () => {
  await load(); setup(); document.body.style.overflow = 'scroll'; const user = userEvent.setup({ document: dom.window.document });
  const view = render(app()); await view.findByRole('button', { name: '첫 합주 추가' });
  const trigger = view.getByRole('button', { name: '합주 추가', exact: true });
  await user.click(trigger); const dialog = view.getByRole('dialog'); const title = view.getByLabelText('제목');
  const heading = within(dialog).getByRole('heading', { name: '새 합주' });
  assert.equal(document.activeElement === heading, true); assert.equal(document.body.style.overflow, 'hidden');
  await user.tab({ shift: true }); assert.equal(document.activeElement === within(dialog).getByRole('button', { name: '저장', exact: true }), true);
  await user.type(title, '버릴 초안');
  fireEvent.pointerDown(dialog.parentElement); assert.equal(view.queryByRole('dialog') !== null, true);
  const close = within(dialog).getByRole('button', { name: '합주 추가 닫기' }); close.focus();
  await user.tab({ shift: true }); assert.equal(document.activeElement === within(dialog).getByRole('button', { name: '저장', exact: true }), true);
  await user.tab(); assert.equal(document.activeElement === close, true);
  await user.keyboard('{Escape}');
  assert.equal(view.queryByRole('dialog') === null, true); assert.equal(document.activeElement === trigger, true); assert.equal(document.body.style.overflow, 'scroll');
  await user.click(view.getByRole('button', { name: '첫 합주 추가' }));
  assert.equal(view.getByLabelText('제목').value, '');
  await user.click(view.getByRole('button', { name: '취소', exact: true }));
  assert.equal(view.queryByRole('dialog') === null, true);
  assert.equal(document.activeElement === trigger, true);
});

test('header create opens during an unresolved list request and a validation failure retains editable inputs', async () => {
  await load(); const mock = setup({ list: 'loading', post: async () => { const error = new Error('invalid'); error.status = 400; throw error; } });
  const view = render(app()); const user = userEvent.setup({ document: dom.window.document });
  await user.click(view.getByRole('button', { name: '합주 추가', exact: true }));
  await view.findByRole('dialog'); await fill(view);
  await user.click(view.getByRole('button', { name: '저장', exact: true })); await view.findByRole('alert');
  assert.equal(view.getByLabelText('제목').value, '새 합주 초안');
  assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, false);
  assert.equal(mock.calls.filter(({ options }) => options.method === 'POST').length, 1);
  await user.click(view.getByRole('button', { name: '취소', exact: true })); assert.equal(view.queryByRole('dialog') === null, true);
});

test('saving blocks every close path and duplicate submits; success closes and refreshes', async () => {
  await load(); let finish; const mock = setup({ post: () => new Promise((resolve) => { finish = resolve; }) });
  const view = render(app('?create=1')); await view.findByRole('dialog'); const user = await fill(view);
  const before = mock.calls.filter(({ path }) => path.includes('/rehearsals?')).length;
  const form = view.getByLabelText('제목').closest('form'); fireEvent.submit(form); fireEvent.submit(form);
  await waitFor(() => assert.equal(mock.calls.filter(({ options }) => options.method === 'POST').length, 1));
  assert.equal(view.getByRole('button', { name: '합주 추가 닫기' }).disabled, true);
  assert.equal(view.getByRole('button', { name: '취소', exact: true }).disabled, true);
  fireEvent.click(view.getByRole('button', { name: '합주 추가 닫기' })); await user.keyboard('{Escape}');
  assert.equal(view.queryByRole('dialog') !== null, true);
  await act(async () => finish({ rehearsal: { id: 9 } }));
  await waitFor(() => assert.equal(view.queryByRole('dialog') === null, true));
  assert.equal(mock.calls.filter(({ path }) => path.includes('/rehearsals?')).length > before, true);
});

test('failed save preserves the draft while candidate unknown result still prevents blind retry', async () => {
  await load(); const mock = setup({ post: async () => { throw new TypeError('connection lost'); } });
  const previous = window.confirm; window.confirm = () => true;
  try {
    const view = render(app('?create=1&date=2026-11-07&period=evening')); await view.findByRole('dialog');
    const user = userEvent.setup({ document: dom.window.document }); await user.type(view.getByLabelText('제목'), '유지할 후보');
    await user.click(view.getByRole('button', { name: '저장', exact: true }));
    await view.findByRole('alert');
    assert.equal(view.getByLabelText('제목').value, '유지할 후보');
    assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true);
    assert.equal(mock.calls.filter(({ path }) => path.includes('/availability?')).length, 1);
    assert.equal(mock.calls.filter(({ options }) => options.method === 'POST').length, 1);
    fireEvent.submit(view.getByLabelText('제목').closest('form'));
    assert.equal(mock.calls.filter(({ options }) => options.method === 'POST').length, 1);
    await user.click(view.getByRole('button', { name: '합주 추가 닫기' })); assert.equal(view.queryByRole('dialog') === null, true);
  } finally { window.confirm = previous; }
});

test('rotation preserves a mobile creation draft and never changes a PC-started pane into a dialog', async () => {
  await load(); const mobile = setup(); const view = render(app('?create=1')); await view.findByRole('dialog');
  const user = await fill(view); const original = view.getByLabelText('제목');
  await mobile.resize(false); assert.equal(view.getByLabelText('제목') === original, true); assert.equal(original.value, '새 합주 초안'); assert.ok(view.getByRole('dialog'));
  await user.click(view.getByRole('button', { name: '취소', exact: true })); cleanup();
  const pc = setup({ mobile: false }); const desktop = render(app('?create=1')); const pcTitle = await desktop.findByLabelText('제목');
  assert.equal(desktop.queryByRole('dialog') === null, true);
  await user.type(pcTitle, 'PC 초안'); await pc.resize(true);
  assert.equal(desktop.queryByRole('dialog') === null, true); assert.equal(desktop.getByLabelText('제목') === pcTitle, true); assert.equal(pcTitle.value, 'PC 초안');
});

test('leaving a pending PC pane aborts its request without blocking cancel in the next creation', async () => {
  await load(); let pendingSignal;
  const mock = setup({ mobile: false, list: 'nonempty', post: (_path, options) => { pendingSignal = options.signal; return new Promise(() => {}); } });
  const view = render(app('?create=1')); await view.findByLabelText('제목'); const user = await fill(view);
  fireEvent.submit(view.getByLabelText('제목').closest('form'));
  await waitFor(() => assert.equal(mock.calls.filter(({ options }) => options.method === 'POST').length, 1));
  await user.click(view.getByRole('button', { name: /기존 합주/ }));
  assert.equal(pendingSignal.aborted, true);
  await user.click(view.getByRole('button', { name: '합주 추가', exact: true }));
  assert.equal(view.getByLabelText('제목').value, '');
  await user.click(view.getByRole('button', { name: '취소', exact: true }));
  assert.equal(view.queryByLabelText('제목') === null, true);
  assert.equal(view.queryByRole('dialog') === null, true);
});
