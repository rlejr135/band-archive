import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { cleanup, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import { normalizeRehearsalType, rehearsalTypeLabel, REHEARSAL_TYPES } from '../../services/rehearsalTypes.js';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const user = userEvent.setup({ document: dom.window.document });
let vite; let form; let detail; let screen; let workspaceContext; let policyContext; let authContext;
const band = { id: 8, slug: 'practice', membership: { role: 'member', status: 'active' }, default_visibility: 'MEMBERS' };
const load = async () => {
  if (form) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  form = (await vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalForm.jsx')).RehearsalForm;
  detail = (await vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalDetail.jsx')).RehearsalDetail;
  screen = (await vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalWorkspace.jsx')).RehearsalWorkspace;
  workspaceContext = (await vite.ssrLoadModule('/src/components/workspace/WorkspaceContext.jsx')).WorkspaceContext;
  policyContext = (await vite.ssrLoadModule('/src/context/ServicePolicyContext.jsx')).ServicePolicyContext;
  authContext = (await vite.ssrLoadModule('/src/context/AuthContext.jsx')).AuthContext;
};
const mockApi = (rehearsals = []) => {
  const writes = [];
  return { writes, api: { requestJson: async (path, options = {}) => {
    if (options.method) { writes.push({ path, method: options.method, body: JSON.parse(options.body) }); return { rehearsal: {} }; }
    if (path.includes('/songs?')) return { songs: [], pagination: { has_more: false } };
    if (path.includes('/media?')) return { media: [], pagination: { has_more: false } };
    return { rehearsals, pagination: { has_more: false } };
  } } };
};
const app = (component, api) => createElement(authContext.Provider, { value: { user: { id: 'user-a' }, getAccessToken: async () => 'test', refreshSession: async () => 'test' } },
  createElement(policyContext.Provider, { value: { config: { policies: { upload: { enabled: false } } } } },
    createElement(workspaceContext.Provider, { value: { band, api, workspaceKey: '8:user-a' } },
      createElement(MemoryRouter, { initialEntries: ['/bands/practice/rehearsals'] }, component))));
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());

test('rehearsal type helper uses the fixed labels and treats absent or unknown values as unclassified', () => {
  assert.deepEqual(REHEARSAL_TYPES, [['REGULAR', '정기합주'], ['EXTRA', '추가합주'], ['LIVE', '라이브공연'], ['OTHER', '기타'], ['UNCLASSIFIED', '미분류']]);
  assert.equal(normalizeRehearsalType(undefined), 'UNCLASSIFIED');
  assert.equal(normalizeRehearsalType('unverified'), 'UNCLASSIFIED');
  assert.equal(rehearsalTypeLabel('LIVE'), '라이브공연');
});

test('current rehearsal form defaults to REGULAR and submits the selected date and time', async () => {
  await load(); const mock = mockApi(); let saved = 0;
  const view = render(app(createElement(form, { onCancel: () => {}, onDone: () => { saved += 1; } }), mock.api));
  assert.equal(view.getByLabelText('합주 유형').value, 'REGULAR');
  await user.type(view.getByLabelText('제목'), '정기 합주');
  const date = view.getByLabelText('날짜');
  const time = view.getByLabelText('시간');
  assert.equal(date.type, 'date');
  assert.equal(date.required, true);
  assert.equal(time.type, 'time');
  assert.equal(time.required, false);
  await user.type(date, '2026-10-30');
  await user.type(time, '19:30');
  await user.click(view.getByRole('button', { name: '저장', exact: true }));
  await waitFor(() => assert.equal(mock.writes.length, 1));
  assert.equal(mock.writes[0].method, 'POST');
  assert.equal(mock.writes[0].path, bandWorkspacePaths.rehearsals(8));
  assert.equal(mock.writes[0].body.rehearsal_type, 'REGULAR');
  assert.equal(mock.writes[0].body.date, '2026-10-30');
  assert.equal(mock.writes[0].body.time, '19:30');
  assert.deepEqual(mock.writes[0].body.song_ids, []);
  assert.equal(Object.hasOwn(mock.writes[0].body, 'band_id'), false);
  assert.equal(saved, 1);
});

test('an existing free-text or second-precision time is preserved until selected or cleared', async () => {
  await load();
  for (const { time, selectTime, clearTime, expected } of [
    { time: '저녁 7시 반', expected: '저녁 7시 반' },
    { time: '19:30:45', expected: '19:30:45' },
    { time: '저녁 7시 반', selectTime: '20:15', expected: '20:15' },
    { time: '저녁 7시 반', clearTime: true, expected: '' },
  ]) {
    const mock = mockApi();
    const item = { id: 44, title: '기존 일정', date: '2026-10-20', rehearsal_type: 'REGULAR', time, songs: [] };
    const view = render(app(createElement(form, { item, onCancel: () => {}, onDone: () => {} }), mock.api));
    const input = view.getByLabelText('시간');
    assert.equal(input.value, '');
    assert.match(view.getByText(/기존 시간은/).textContent, new RegExp(time));
    if (selectTime) await user.type(input, selectTime);
    if (clearTime) await user.click(view.getByRole('button', { name: '기존 시간 지우기' }));
    await user.click(view.getByRole('button', { name: '저장', exact: true }));
    await waitFor(() => assert.equal(mock.writes.length, 1));
    assert.equal(mock.writes[0].method, 'PATCH');
    assert.equal(mock.writes[0].body.time, expected);
    view.unmount();
  }
});

test('current rehearsal edit preserves an absent or unknown type while keeping existing fields', async () => {
  await load();
  for (const rehearsalType of [undefined, 'future-type']) {
    const mock = mockApi();
    const item = { id: 44, title: '기존 일정', date: '2026-10-20', time: '19:00', location: '연습실', memo: '기존 메모', color: '#0fbcf9', songs: [], rehearsal_type: rehearsalType };
    const view = render(app(createElement(form, { item, onCancel: () => {}, onDone: () => {} }), mock.api));
    assert.equal(view.getByLabelText('합주 유형').value, 'UNCLASSIFIED');
    assert.equal(view.queryByText(/기존 합주 유형은 추정하지 않았습니다/) !== null, true);
    await user.type(view.getByLabelText('제목'), ' 수정');
    await user.click(view.getByRole('button', { name: '저장', exact: true }));
    await waitFor(() => assert.equal(mock.writes.length, 1));
    assert.equal(mock.writes[0].method, 'PATCH');
    assert.equal(mock.writes[0].path, '/api/v2/bands/8/rehearsals/44');
    assert.equal(Object.hasOwn(mock.writes[0].body, 'rehearsal_type'), false);
    assert.equal(mock.writes[0].body.time, '19:00');
    assert.equal(mock.writes[0].body.location, '연습실');
    assert.equal(mock.writes[0].body.memo, '기존 메모');
    assert.equal(mock.writes[0].body.color, '#0fbcf9');
    view.unmount();
  }
});

test('current rehearsal edit sends an explicitly selected LIVE type', async () => {
  await load(); const mock = mockApi();
  const item = { id: 44, title: '기존 일정', date: '2026-10-20', songs: [] };
  const view = render(app(createElement(form, { item, onCancel: () => {}, onDone: () => {} }), mock.api));
  await user.selectOptions(view.getByLabelText('합주 유형'), 'LIVE');
  await user.click(view.getByRole('button', { name: '저장', exact: true }));
  await waitFor(() => assert.equal(mock.writes.length, 1));
  assert.equal(mock.writes[0].body.rehearsal_type, 'LIVE');
});

test('current rehearsal details display LIVE and missing types without guessing', async () => {
  await load();
  for (const [rehearsalType, label, normalized] of [['LIVE', '라이브공연', 'LIVE'], [undefined, '미분류', 'UNCLASSIFIED']]) {
    const mock = mockApi();
    const item = { id: 1, title: '상세 일정', date: '2026-10-20', rehearsal_type: rehearsalType, songs: [] };
    const view = render(app(createElement(detail, { item, onEdit: () => {}, onDeleted: () => {} }), mock.api));
    assert.equal(view.getByText(label).getAttribute('data-rehearsal-type'), normalized);
    await view.findByText(/표시할 미디어가 없습니다/);
    view.unmount();
  }
});

test('current rehearsal list exposes a text badge for classified and missing types', async () => {
  await load();
  const mock = mockApi([
    { id: 1, title: '라이브 일정', date: '2026-10-20', rehearsal_type: 'LIVE', songs: [] },
    { id: 2, title: '예전 합주', date: '2026-10-21', songs: [] },
  ]);
  const view = render(app(createElement(screen), mock.api));
  await view.findByRole('button', { name: /라이브 일정/ });
  assert.equal(view.getByText('라이브공연').getAttribute('data-rehearsal-type'), 'LIVE');
  assert.equal(view.getByText('미분류').getAttribute('data-rehearsal-type'), 'UNCLASSIFIED');
  await user.click(view.getByRole('button', { name: /예전 합주/ }));
  await view.findByRole('heading', { name: '예전 합주' });
  assert.equal(view.getAllByText('미분류').length, 2);
});
