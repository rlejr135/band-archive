import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';
import { monthDays, seoulToday } from './availabilityState.js';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
dom.window.PointerEvent = class extends dom.window.MouseEvent { constructor(type, options = {}) { super(type, options); Object.defineProperty(this, 'pointerType', { configurable: true, value: options.pointerType || 'mouse' }); } };
let vite; let Calendar;
const dates = monthDays(seoulToday().slice(0, 7)); const date = dates[0]; const otherDate = dates[1];
const snapshot = (overrides = {}) => ({ band_id: 8, generation: 0, last_reset_at: null, can_reset: false, reset_summary: { days: 1, notes: 0 }, members: [{ membership_id: 12, display_name: '나' }, { membership_id: 13, display_name: '동료' }], days: [{ membership_id: 12, date, morning: 'UNAVAILABLE', afternoon: 'UNKNOWN', evening: 'AVAILABLE', revision: 3 }], notes: [], my_membership_id: 12, ...overrides });
const band = { id: 8, slug: 'test-band', name: '테스트 밴드', membership: { id: 12, status: 'active', role: 'member' } };
const load = async () => { if (Calendar) return; vite = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); Calendar = (await vite.ssrLoadModule('/src/components/workspace/AvailabilityCalendar.jsx')).default; };
const app = (api, props = {}) => createElement(MemoryRouter, null, createElement(Calendar, { band, api, workspaceKey: '8:user', ...props }));
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const day = (view, value) => view.getByRole('button', { name: `${value} 일정 선택` });
const chooseRange = (view, from, to) => { fireEvent.pointerDown(day(view, from), { pointerType: 'mouse', button: 0 }); fireEvent.pointerEnter(day(view, to), { pointerType: 'mouse' }); fireEvent.pointerUp(window); };
const openApply = (view) => { const button = view.getByRole('button', { name: '적용', exact: true }); button.focus(); fireEvent.click(button); return view.getByRole('dialog', { name: '선택한 날짜에 적용' }); };
const choosePeriods = (dialog, periods) => { const group = within(dialog).getByRole('group', { name: '적용할 시간대' }); periods.forEach((p) => fireEvent.click(within(group).getByRole('button', { name: new RegExp(`^${p}`) }))); };
afterEach(() => cleanup()); after(async () => { await vite?.close(); dom.window.close(); });

test('inactive membership makes no calendar request', async () => {
  await load(); let calls = 0; const view = render(app({ requestJson: async () => { calls += 1; } }, { band: { ...band, membership: null } }));
  assert.ok(view.getByText('활성 밴드 멤버만 일정을 조회하고 등록할 수 있습니다.')); assert.equal(calls, 0);
});
test('Sunday-first month calendar pads boundaries and reverse drag crosses week rows before explicit apply/save', async () => {
  await load(); const calls = []; const api = { requestJson: async (url, options = {}) => { calls.push({ url, options }); return options.method ? { changed: true, generation: 0 } : snapshot(); } };
  const view = render(app(api)); await view.findByRole('button', { name: `${date} 일정 선택` });
  assert.deepEqual([...view.container.querySelectorAll('.availability-month-grid thead th')].map((th) => th.textContent), ['일', '월', '화', '수', '목', '금', '토']);
  assert.equal(view.container.querySelectorAll('[data-availability-day]').length, dates.length); assert.equal(view.container.querySelectorAll('input[type=date]').length, 0);
  assert.equal(view.container.querySelectorAll('.availability-empty-day button').length, 0);
  chooseRange(view, dates[7], date); assert.equal(view.container.querySelectorAll('.availability-day[aria-pressed=true]').length, 8);
  assert.equal(calls.filter(({ options }) => options.method).length, 0);
  const dialog = openApply(view); assert.equal(calls.filter(({ options }) => options.method).length, 0);
  const save = within(dialog).getByRole('button', { name: '저장', exact: true }); assert.equal(save.disabled, true);
  choosePeriods(dialog, ['오후', '저녁']); fireEvent.click(save);
  await waitFor(() => assert.equal(calls.filter(({ options }) => options.method).length, 1));
  const write = calls.find(({ options }) => options.method); const payload = JSON.parse(write.options.body);
  assert.match(write.url, /\/availability\/me\/batch$/); assert.equal(payload.generation, 0); assert.equal(payload.days.length, 8);
  assert.deepEqual(payload.days[0], { date, revision: 3, morning: 'UNAVAILABLE', afternoon: 'AVAILABLE', evening: 'AVAILABLE' });
  await view.findByText('날짜를 선택해 주세요');
});
test('apply modal cancels without mutation, traps focus and restores apply focus on Escape', async () => {
  await load(); let writes = 0; const view = render(app({ requestJson: async (_url, options = {}) => { if (options.method) writes += 1; return snapshot(); } }));
  await view.findByRole('button', { name: `${date} 일정 선택` }); chooseRange(view, date, otherDate); const dialog = openApply(view);
  const first = within(dialog).getByRole('button', { name: /^오전/ }); assert.equal(document.activeElement, first);
  fireEvent.keyDown(first, { key: 'Tab', shiftKey: true }); assert.equal(document.activeElement, within(dialog).getByRole('button', { name: '취소', exact: true }));
  choosePeriods(dialog, ['오전']); fireEvent.click(within(dialog).getByRole('button', { name: '취소', exact: true })); assert.equal(view.queryByRole('dialog'), null); assert.equal(writes, 0);
  const again = openApply(view); assert.equal(within(again).getByRole('button', { name: '저장', exact: true }).disabled, true);
  fireEvent.keyDown(again, { key: 'Escape' }); assert.equal(view.queryByRole('dialog'), null); assert.equal(document.activeElement, view.getByRole('button', { name: '적용', exact: true })); assert.equal(document.body.style.overflow, '');
  fireEvent.click(view.getByRole('button', { name: '선택 취소' })); assert.equal(view.getByRole('button', { name: '적용', exact: true }).disabled, true);
});
test('keyboard range, single status selection and 409 discard stale selection then reload generation', async () => {
  await load(); let gets = 0; const writes = []; const api = { requestJson: async (_url, options = {}) => { if (options.method) { writes.push(JSON.parse(options.body)); const error = new Error('Conflict'); error.status = 409; throw error; } return snapshot({ generation: gets++ ? 1 : 0 }); } };
  const view = render(app(api)); await view.findByRole('button', { name: `${date} 일정 선택` });
  fireEvent.keyDown(day(view, date), { key: ' ' }); fireEvent.keyDown(day(view, dates[7]), { key: 'Enter', shiftKey: true });
  assert.equal(view.container.querySelectorAll('.availability-day[aria-pressed=true]').length, 8);
  const dialog = openApply(view); choosePeriods(dialog, ['오전']);
  const statuses = within(dialog).getByRole('group', { name: '적용할 상태' }); fireEvent.click(within(statuses).getByRole('button', { name: '불가', exact: true }));
  assert.equal(within(statuses).getByRole('button', { name: '가능', exact: true }).getAttribute('aria-pressed'), 'false');
  fireEvent.click(within(dialog).getByRole('button', { name: '저장', exact: true }));
  await view.findByText(/다른 기기에서 수정했거나 owner가 초기화했습니다/); await waitFor(() => assert.equal(gets, 2)); assert.equal(writes[0].days[0].morning, 'UNAVAILABLE');
  assert.equal(view.queryByRole('dialog'), null); assert.equal(view.getByRole('button', { name: '적용', exact: true }).disabled, true);
});
test('month navigation clears date selection and does not save', async () => {
  await load(); const calls = []; const view = render(app({ requestJson: async (_url, options = {}) => { calls.push(options); return snapshot(); } }));
  await view.findByRole('button', { name: `${date} 일정 선택` }); chooseRange(view, date, otherDate); fireEvent.click(view.getByRole('button', { name: '다음 달' }));
  await view.findByText('날짜를 선택해 주세요'); assert.equal(calls.filter((options) => options.method).length, 0); assert.equal(view.getByRole('button', { name: '적용', exact: true }).disabled, true);
});
test('owner reset still requires explicit confirmation and preserves separate permissions', async () => {
  await load(); const writes = []; const api = { requestJson: async (_url, options = {}) => { if (options.method) writes.push(options); return options.method ? { generation: 1 } : snapshot({ can_reset: true }); } };
  const view = render(app(api)); await view.findByRole('button', { name: '모두의 조율 일정 초기화' }); fireEvent.click(view.getByRole('button', { name: '모두의 조율 일정 초기화' }));
  assert.ok(view.getByRole('alertdialog', { name: '전체 조율 일정 초기화 확인' })); assert.equal(writes.length, 0);
  fireEvent.click(view.getByRole('button', { name: '확인하고 전체 초기화' })); await waitFor(() => assert.equal(writes.length, 1)); assert.deepEqual(JSON.parse(writes[0].body), { generation: 0, confirm: true });
});
test('private note remains opt-in plain text and deleted revision is reused', async () => {
  await load(); const writes = []; const api = { requestJson: async (_url, options = {}) => { if (options.method) writes.push(options); return options.method ? {} : snapshot({ notes: [{ membership_id: 12, date, period: 'evening', revision: 7, body: null, visibility: 'PRIVATE' }, { membership_id: 13, date, period: 'evening', revision: 1, body: '<img onerror=alert(1)>', visibility: 'BAND' }] }); } };
  const view = render(app(api)); await view.findByRole('button', { name: `${date} 일정 선택` }); fireEvent.keyDown(day(view, date), { key: 'Enter' });
  fireEvent.click(view.getByText('선택 개인 일정 메모')); const body = view.getByLabelText('개인 일정 (선택)'); assert.equal(view.getByLabelText('이 메모를 같은 밴드 멤버에게 공유').checked, false);
  await userEvent.type(body, '잠시 일정'); fireEvent.click(view.getByRole('button', { name: '메모 저장' })); await waitFor(() => assert.equal(writes.length, 1));
  assert.deepEqual(JSON.parse(writes[0].body), { generation: 0, revision: 7, body: '잠시 일정', visibility: 'PRIVATE' }); assert.equal(view.container.querySelector('img'), null);
});
test('late old band response is aborted and cannot replace next band state', async () => {
  await load(); const old = deferred(); let oldSignal; const api = { requestJson: async (url, options = {}) => { if (url.includes('/8/')) { oldSignal = options.signal; return old.promise; } return snapshot({ band_id: 9, members: [{ membership_id: 14, display_name: '새 밴드 멤버' }], my_membership_id: 14, days: [] }); } };
  const view = render(app(api)); await waitFor(() => assert.ok(oldSignal)); view.rerender(app(api, { band: { ...band, id: 9, slug: 'next' }, workspaceKey: '9:user' })); await view.findAllByText('새 밴드 멤버'); assert.equal(oldSignal.aborted, true);
  await act(async () => { old.resolve(snapshot()); await old.promise; }); assert.equal(view.queryByText('동료'), null);
});
test('touch scrolling is free until selection mode, then hit-testing selects inclusive dates', async () => {
  await load(); const view = render(app({ requestJson: async () => snapshot() })); await view.findByRole('button', { name: `${date} 일정 선택` });
  fireEvent.pointerDown(day(view, date), { pointerType: 'touch', button: 0 }); fireEvent.pointerEnter(day(view, dates[7]), { pointerType: 'touch' }); assert.equal(view.getByRole('button', { name: '적용', exact: true }).disabled, true);
  fireEvent.click(view.getByLabelText(/터치 범위 선택 모드/)); const originalHit = document.elementFromPoint; document.elementFromPoint = () => day(view, dates[7]);
  try { fireEvent.pointerDown(day(view, date), { pointerType: 'touch', button: 0 }); fireEvent.pointerMove(day(view, date), { pointerType: 'touch', clientX: 30, clientY: 40 }); fireEvent.pointerUp(window); } finally { document.elementFromPoint = originalHit; }
  assert.equal(view.container.querySelectorAll('.availability-day[aria-pressed=true]').length, 8);
});
test('pending modal save is single-flight and account transition aborts late completion', async () => {
  await load(); const write = deferred(); const calls = []; let signal;
  const api = { requestJson: async (_url, options = {}) => { calls.push(options); if (options.method) { signal = options.signal; return write.promise; } return snapshot(); } };
  const view = render(app(api)); await view.findByRole('button', { name: `${date} 일정 선택` }); fireEvent.keyDown(day(view, date), { key: 'Enter' }); const dialog = openApply(view); choosePeriods(dialog, ['저녁']); const save = within(dialog).getByRole('button', { name: '저장', exact: true }); fireEvent.click(save); fireEvent.click(save);
  await waitFor(() => assert.ok(signal)); assert.equal(calls.filter((options) => options.method).length, 1);
  view.rerender(app(api, { workspaceKey: '8:other-user', band: { ...band, membership: null } })); assert.equal(signal.aborted, true); await act(async () => { write.resolve({ changed: true, generation: 0 }); await write.promise; }); assert.equal(view.queryByRole('dialog'), null);
});
