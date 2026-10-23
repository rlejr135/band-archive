import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { createServer } from 'vite';
import { normalizeRehearsalType, rehearsalTypeLabel, REHEARSAL_TYPES } from '../../services/rehearsalTypes.js';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const user = userEvent.setup({ document: dom.window.document });
let vite; let modal; let detail; let calendar;
const mediaUploadDouble = { uploadBatch: async () => {}, retryAudio: async () => {}, retryTask: async () => {}, cancelByNativeId: async () => {}, transport: null, nativePending: [], subscribeMediaCompletion: () => () => {} };
globalThis.__mediaUploadDouble = mediaUploadDouble;
const load = async () => {
  if (modal) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true }, plugins: [{ name: 'rehearsal-dom-doubles', enforce: 'pre', transform(_code, id) {
    const path = id.replaceAll('\\', '/');
    if (path.endsWith('/src/services/rehearsalApi.js')) return `export const createRehearsal = async (data) => { globalThis.rehearsalCalls.push(['create', null, data]); }; export const updateRehearsal = async (id, data) => { globalThis.rehearsalCalls.push(['update', id, data]); }; export const fetchRehearsals = async () => globalThis.calendarRehearsals || [];`;
    if (path.endsWith('/src/hooks/useMediaUpload.js')) return `const mediaUploadDouble = globalThis.__mediaUploadDouble; export default () => mediaUploadDouble;`;
    if (path.endsWith('/src/context/SongContext.jsx')) return `export const useSongs = () => ({ songs: [] });`;
  } }] });
  modal = (await vite.ssrLoadModule('/src/components/calendar/RehearsalModal.jsx')).default;
  detail = (await vite.ssrLoadModule('/src/components/calendar/RehearsalDetail.jsx')).default;
  calendar = (await vite.ssrLoadModule('/src/components/calendar/RehearsalCalendar.jsx')).default;
};
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => { cleanup(); globalThis.rehearsalCalls = []; });

test('rehearsal type helper uses the fixed labels and treats absent or unknown values as unclassified', () => {
  assert.deepEqual(REHEARSAL_TYPES, [['REGULAR', '정기합주'], ['EXTRA', '추가합주'], ['LIVE', '라이브공연'], ['OTHER', '기타'], ['UNCLASSIFIED', '미분류']]);
  assert.equal(normalizeRehearsalType(undefined), 'UNCLASSIFIED');
  assert.equal(normalizeRehearsalType('unverified'), 'UNCLASSIFIED');
  assert.equal(rehearsalTypeLabel('LIVE'), '라이브공연');
});

test('new rehearsal explicitly defaults to REGULAR and sends type without changing other form fields', async () => {
  await load(); globalThis.rehearsalCalls = []; let saved = 0;
  const view = render(createElement(modal, { rehearsal: null, songs: [], defaultDate: new Date(2026, 9, 30), onClose: () => {}, onSave: () => { saved += 1; } }));
  const title = view.getByLabelText('제목 *'); const date = view.container.querySelector('input[type="date"]'); const type = view.getByLabelText('합주 유형');
  assert.equal(type.value, 'REGULAR'); await user.type(title, '정기 합주'); fireEvent.change(date, { target: { value: '2026-10-30' } });
  await user.click(view.getByRole('button', { name: '추가' })); await waitFor(() => assert.equal(globalThis.rehearsalCalls.length, 1));
  assert.deepEqual(globalThis.rehearsalCalls[0][0], 'create');
  assert.equal(globalThis.rehearsalCalls[0][2].rehearsal_type, 'REGULAR');
  assert.equal(globalThis.rehearsalCalls[0][2].date, '2026-10-30');
  assert.equal(globalThis.rehearsalCalls[0][2].color, '#ffd32a');
  assert.deepEqual(globalThis.rehearsalCalls[0][2].song_ids, []); assert.equal(saved, 1);
});

test('editing an unclassified rehearsal preserves its type unless changed and then sends the selected enum', async () => {
  await load(); globalThis.rehearsalCalls = [];
  const rehearsal = { id: 44, title: '기존 일정', date: '2026-10-20', time: '19:00', location: '연습실', memo: '기존 메모', color: '#0fbcf9', songs: [] };
  const view = render(createElement(modal, { rehearsal, songs: [], defaultDate: null, onClose: () => {}, onSave: () => {} }));
  const type = await view.findByLabelText('합주 유형'); await waitFor(() => assert.equal(type.value, 'UNCLASSIFIED'));
  assert.ok(view.getByText(/기존 일정의 유형은 추정하지 않았습니다/));
  await user.type(view.getByLabelText('제목 *'), ' 수정'); await user.click(view.getByRole('button', { name: '수정' }));
  await waitFor(() => assert.equal(globalThis.rehearsalCalls.length, 1));
  assert.equal(globalThis.rehearsalCalls[0][0], 'update'); assert.equal(globalThis.rehearsalCalls[0][1], 44);
  assert.equal(Object.hasOwn(globalThis.rehearsalCalls[0][2], 'rehearsal_type'), false);
  assert.equal(globalThis.rehearsalCalls[0][2].time, '19:00'); assert.equal(globalThis.rehearsalCalls[0][2].location, '연습실');
  view.unmount(); globalThis.rehearsalCalls = [];
  const changed = render(createElement(modal, { rehearsal, songs: [], defaultDate: null, onClose: () => {}, onSave: () => {} }));
  await waitFor(() => assert.equal(changed.getByLabelText('합주 유형').value, 'UNCLASSIFIED'));
  await user.selectOptions(changed.getByLabelText('합주 유형'), 'LIVE'); await user.click(changed.getByRole('button', { name: '수정' }));
  await waitFor(() => assert.equal(globalThis.rehearsalCalls.length, 1));
  assert.equal(globalThis.rehearsalCalls[0][2].rehearsal_type, 'LIVE');
});

test('selected calendar details display a consistent type badge and missing types remain unclassified', async () => {
  await load(); globalThis.__mediaUploadDouble = mediaUploadDouble;
  const view = render(createElement(detail, { date: new Date(2026, 9, 20), rehearsals: [
    { id: 1, title: '라이브 일정', date: '2026-10-20', rehearsal_type: 'LIVE', songs: [] },
    { id: 2, title: '기존 일정', date: '2026-10-20', songs: [] },
  ], onEdit: () => {}, onDelete: () => {}, onAdd: () => {} }));
  const live = view.getByText('라이브공연'); const unclassified = view.getByText('미분류');
  assert.equal(live.getAttribute('data-rehearsal-type'), 'LIVE');
  assert.equal(unclassified.getAttribute('data-rehearsal-type'), 'UNCLASSIFIED');
  assert.ok(view.getByText('라이브 일정')); assert.ok(view.getByText('기존 일정'));
  await act(async () => {});
});

test('calendar date markers expose the event type as a hover and assistive label', async () => {
  await load();
  const date = new Date(); const dateString = date.toLocaleDateString('en-CA');
  globalThis.calendarRehearsals = [{ id: 7, title: '예전 합주', date: dateString, color: '#ffd32a' }];
  const view = render(createElement(calendar));
  await waitFor(() => assert.ok(view.container.querySelector('[title="미분류: 예전 합주"]')));
  const marker = view.container.querySelector('[title="미분류: 예전 합주"]');
  assert.equal(marker.getAttribute('aria-label'), '미분류: 예전 합주');
});
