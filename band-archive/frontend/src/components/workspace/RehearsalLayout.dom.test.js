import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
// React DOM must detect the actual DOM event capabilities, not its server fallback.
const { createElement: h, useState } = await import('react');
const { cleanup, fireEvent, render, waitFor, act } = await import('@testing-library/react');
const { default: userEvent } = await import('@testing-library/user-event');
const { MemoryRouter } = await import('react-router-dom');
let vite; let Workspace; let Picker;
const load = async () => {
  if (vite) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true }, plugins: [{ name: 'rehearsal-layout-doubles', enforce: 'pre', transform(_code, id) {
    const path = id.replaceAll('\\', '/');
    if (path.endsWith('/workspace/WorkspaceContext.jsx')) return 'export const useWorkspace = () => globalThis.__rehearsalWorkspace;';
    if (path.endsWith('/workspace/AvailabilityCalendar.jsx')) return 'export const RehearsalTabs = () => null;';
    if (path.endsWith('/rehearsals/RehearsalDetail.jsx')) return `import React from 'react'; export const RehearsalDetail = ({item}) => React.createElement('p', null, '상세 '+item.title);`;
    if (path.endsWith('/rehearsals/RehearsalForm.jsx')) return 'export const RehearsalForm = () => null;';
  } }] });
  Workspace = (await vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalWorkspace.jsx')).RehearsalWorkspace;
  Picker = (await vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalSongPicker.jsx')).RehearsalSongPicker;
};
afterEach(() => cleanup()); after(async () => { await vite?.close(); dom.window.close(); delete globalThis.__rehearsalWorkspace; });
const screen = () => h(MemoryRouter, null, h(Workspace));
const setup = (mobile) => {
  window.matchMedia = () => ({ matches: mobile, addEventListener() {}, removeEventListener() {} });
  globalThis.__rehearsalWorkspace = { band: { id: 1, slug: 'one', membership: { role: 'owner' } }, workspaceKey: 'one:user', api: { requestJson: async () => ({ rehearsals: [{ id: 2, title: '최신 합주', date: '2026-11-06' }, { id: 1, title: '이전 합주', date: '2026-10-01' }], pagination: { has_more: false } }) } };
};

test('mobile opens exactly one detail directly inside the selected card and collapses it', async () => {
  await load(); setup(true); const view = render(screen());
  const first = await view.findByRole('button', { name: /최신 합주/ });
  fireEvent.click(first);
  assert.equal(first.getAttribute('aria-expanded'), 'true');
  assert.equal(view.getAllByRole('region', { name: '합주 상세' }).length, 1);
  assert.ok(first.closest('li').contains(view.getByText('상세 최신 합주')));
  assert.equal(document.getElementById(first.getAttribute('aria-controls')) === view.getByRole('region', { name: '합주 상세' }), true);
  fireEvent.click(view.getByRole('button', { name: /이전 합주/ }));
  assert.equal(view.queryByText('상세 최신 합주') === null, true);
  fireEvent.click(view.getByRole('button', { name: /이전 합주/ }));
  assert.equal(view.queryByText('상세 이전 합주') === null, true);
});

test('desktop keeps detail alongside the list and band changes discard selected IDs', async () => {
  await load(); setup(false); const view = render(screen());
  const first = await view.findByRole('button', { name: /최신 합주/ }); fireEvent.click(first);
  assert.equal(first.closest('li').contains(view.getByText('상세 최신 합주')), false);
  globalThis.__rehearsalWorkspace = { ...globalThis.__rehearsalWorkspace, band: { id: 2, slug: 'two', membership: {} }, workspaceKey: 'two:user' };
  view.rerender(screen());
  assert.equal(view.queryByText('상세 최신 합주') === null, true);
});

test('song picker follows all pages and filtering retains selected hidden songs', async () => {
  await load(); const offsets = []; const user = userEvent.setup({ document: dom.window.document });
  const api = { requestJson: async (path) => { const offset = Number(new URL(path, 'http://localhost').searchParams.get('offset')); offsets.push(offset); return offset === 0 ? { songs: [{ id: 1, title: '긴 곡 이름', artist: '첫 아티스트' }], pagination: { has_more: true, next_offset: 100 } } : { songs: [{ id: 2, title: '두번째 곡', artist: '다른 아티스트' }], pagination: { has_more: false } }; } };
  const Form = () => { const [ids, setIds] = useState(['1']); return h(Picker, { bandId: 1, api, selectedIds: ids, onChange: setIds }); };
  const view = render(h(Form)); await view.findByRole('checkbox', { name: /두번째 곡/ });
  assert.deepEqual(offsets, [0, 100]);
  await user.type(view.getByRole('searchbox'), '다른');
  await waitFor(() => assert.equal(view.queryByRole('checkbox', { name: /긴 곡 이름/ }) === null, true));
  fireEvent.click(view.getByRole('checkbox', { name: /두번째 곡/ }));
  assert.ok(view.getByText('2곡 선택'));
  await user.clear(view.getByRole('searchbox'));
  await waitFor(() => assert.equal(view.getByRole('checkbox', { name: /긴 곡 이름/ }).checked, true));
});

test('song picker aborts a previous band load and ignores its late result', async () => {
  await load(); let finish; let finishNew; let oldSignal;
  const user = userEvent.setup({ document: dom.window.document });
  const api = { requestJson: (path, options) => path.includes('/bands/1/') ? new Promise((resolve) => { finish = resolve; oldSignal = options.signal; }) : new Promise((resolve) => { finishNew = resolve; }) };
  const view = render(h(Picker, { bandId: 1, api, initialSongs: [{ id: 99, title: '이전 밴드 초기 곡' }, { id: 1, title: '이전 밴드 같은 ID' }], selectedIds: [], onChange() {} }));
  await user.type(view.getByRole('searchbox'), '이전');
  assert.equal(view.getByRole('searchbox').value, '이전');
  view.rerender(h(Picker, { bandId: 2, api, initialSongs: [{ id: 2, title: '새 밴드 초기 곡' }, { id: 1, title: '새 밴드 같은 ID' }], selectedIds: [], onChange() {} }));
  assert.equal(view.getByRole('searchbox').value, '');
  assert.ok(view.getByRole('status'));
  assert.equal(view.queryByText('이전 밴드 초기 곡') === null, true);
  assert.equal(view.queryByText('이전 밴드 같은 ID') === null, true);
  assert.ok(view.getByRole('checkbox', { name: /새 밴드 초기 곡/ }));
  assert.ok(view.getByRole('checkbox', { name: /새 밴드 같은 ID/ }));
  assert.equal(oldSignal.aborted, true);
  await act(async () => finish({ songs: [{ id: 99, title: '이전 밴드 곡' }] }));
  assert.equal(view.queryByText('이전 밴드 곡') === null, true);
  await act(async () => finishNew({ songs: [{ id: 1, title: '새 밴드 곡' }], pagination: { has_more: false } }));
  await view.findByRole('checkbox', { name: /새 밴드 곡/ });
  assert.ok(view.getByRole('checkbox', { name: /새 밴드 초기 곡/ }));
  assert.equal(view.queryByText('이전 밴드 초기 곡') === null, true);
});
