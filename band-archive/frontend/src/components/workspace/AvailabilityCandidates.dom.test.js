import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';
import { monthDays, seoulToday } from './availabilityState.js';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
const { createElement: h, Fragment } = await import('react');
const { act, cleanup, fireEvent, render, waitFor, within } = await import('@testing-library/react');
const { MemoryRouter, useLocation } = await import('react-router-dom');
dom.window.PointerEvent = class extends dom.window.MouseEvent { constructor(type, options = {}) { super(type, options); Object.defineProperty(this, 'pointerType', { configurable: true, value: options.pointerType || 'mouse' }); } };
let vite; let Calendar;
const date = seoulToday(); const dates = monthDays(date.slice(0, 7));
const snapshot = (overrides = {}) => ({ band_id: 8, generation: 0, my_membership_id: 12, can_reset: false, notes: [], members: [{ membership_id: 12, display_name: '베이스 이름' }, { membership_id: 13, display_name: '보컬 이름' }], days: [{ membership_id: 12, date, revision: 1, morning: 'UNAVAILABLE', afternoon: 'AVAILABLE', evening: 'AVAILABLE' }, { membership_id: 13, date, revision: 1, morning: 'UNAVAILABLE', afternoon: 'UNKNOWN', evening: 'AVAILABLE' }], ...overrides });
const band = { id: 8, slug: 'test-band', name: '밴드', membership: { id: 12, status: 'active', role: 'owner' } };
const load = async () => { if (Calendar) return; vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); Calendar = (await vite.ssrLoadModule('/src/components/workspace/AvailabilityCalendar.jsx')).default; };
const Location = () => { const location = useLocation(); return h('output', { 'data-testid': 'location' }, location.pathname + location.search); };
const app = (api, props = {}) => h(MemoryRouter, { initialEntries: ['/bands/test-band/availability'] }, h(Fragment, null, h(Calendar, { band, api, workspaceKey: '8:owner', ...props }), h(Location)));
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const dateButton = (view, day = date) => view.getByRole('button', { name: `${day} 후보 시간대별 멤버 가능 여부 보기` });
const ready = async (view) => view.findByRole('button', { name: `${date} 후보 시간대별 멤버 가능 여부 보기` });
afterEach(() => cleanup()); after(async () => { await vite?.close(); dom.window.close(); });

test('candidate dates stay concise, hover reads snapshot names and highest-ranked default period without requests', async () => {
  await load(); let reads = 0; const view = render(app({ requestJson: async () => { reads += 1; return snapshot(); } })); await ready(view);
  const group = view.getByRole('group', { name: '합주 후보 날짜' });
  assert.equal(group.querySelectorAll('.availability-candidate-date-button').length, Math.min(6, dates.filter((day) => day >= date).length));
  assert.equal(dateButton(view).textContent, date); assert.equal(view.queryByText(/선택 시간대로 합주 추가 화면/ ) === null, true);
  assert.equal(view.container.querySelector('.availability-candidate-filter').open, false);
  fireEvent.pointerEnter(dateButton(view).parentElement, { pointerType: 'mouse' });
  const detail = view.getByRole('dialog', { name: `${date} 후보 상세` });
  assert.equal(within(detail).getByRole('button', { name: /저녁 가능/ }).getAttribute('aria-pressed'), 'true');
  assert.ok(within(detail).getByText('베이스 이름 (나)')); assert.ok(within(detail).getByText('보컬 이름'));
  assert.equal(within(detail).getAllByText('O 가능').length, 2);
  fireEvent.click(within(detail).getByRole('button', { name: /오후 가능/ }));
  assert.ok(within(detail).getByText('X 불가능')); assert.ok(within(detail).getByRole('button', { name: `${date} 오후 확인` }));
  assert.equal(reads, 1);
});

test('keyboard focus preserves hovered interactive details and Escape never steals unrelated note focus', async () => {
  await load(); const view = render(app({ requestJson: async () => snapshot() })); await ready(view);
  fireEvent.focus(dateButton(view)); let detail = view.getByRole('dialog');
  const period = within(detail).getByRole('button', { name: /오전 가능/ }); period.focus();
  fireEvent.pointerLeave(dateButton(view).parentElement); assert.equal(view.queryByRole('dialog') !== null, true);
  fireEvent.keyDown(period, { key: 'Escape' }); assert.equal(document.activeElement === dateButton(view), true);
  assert.equal(view.queryByRole('dialog') === null, true);
  fireEvent.click(view.getByText('선택 개인 일정 메모')); const note = view.getByLabelText('개인 일정 (선택)'); note.focus();
  fireEvent.pointerEnter(dateButton(view).parentElement, { pointerType: 'mouse' }); detail = view.getByRole('dialog'); assert.ok(detail);
  fireEvent.keyDown(note, { key: 'Escape' }); assert.equal(document.activeElement === note, true); assert.equal(view.queryByRole('dialog') === null, true);
});

test('tap pin closes on retap, close, outside and cell inspection; filtering clears stale detail', async () => {
  await load(); const view = render(app({ requestJson: async () => snapshot() })); await ready(view);
  fireEvent.click(dateButton(view)); assert.ok(view.getByRole('dialog')); fireEvent.click(dateButton(view)); assert.equal(view.queryByRole('dialog') === null, true);
  fireEvent.click(dateButton(view)); const close = view.getByRole('button', { name: '후보 상세 닫기' }); close.focus(); fireEvent.click(close);
  assert.equal(document.activeElement === dateButton(view), true); assert.equal(view.queryByRole('dialog') === null, true);
  fireEvent.click(dateButton(view)); fireEvent.pointerDown(document.body); assert.equal(view.queryByRole('dialog') === null, true);
  fireEvent.click(dateButton(view)); fireEvent.click(view.container.querySelector(`[data-availability-slot][data-date="${date}"][data-period="morning"]`));
  assert.equal(view.queryByRole('dialog', { name: `${date} 후보 상세` }) === null, true); assert.equal(view.getAllByRole('dialog').length, 1);
  fireEvent.click(view.getByText('후보 계산 대상', { selector: 'summary' }));
  fireEvent.click(dateButton(view)); fireEvent.click(view.getByRole('checkbox', { name: '보컬 이름' })); assert.equal(view.queryByRole('dialog') === null, true);
  fireEvent.click(dateButton(view)); const detail = view.getByRole('dialog'); assert.equal(within(detail).queryByText('보컬 이름') === null, true); assert.ok(within(detail).getByText(/선택한 멤버 1명/));
});

test('members may inspect selected names but only owner/admin get a confirm action', async () => {
  await load(); const api = { requestJson: async () => snapshot() }; const view = render(app(api, { band: { ...band, membership: { ...band.membership, role: 'member' } } })); await ready(view);
  fireEvent.click(dateButton(view)); const detail = view.getByRole('dialog'); assert.ok(within(detail).getByText('베이스 이름 (나)'));
  assert.equal(within(detail).queryByRole('button', { name: /확인/ }) === null, true);
});

test('confirm checks latest fingerprint once, locks other dates, then navigates with the selected period', async () => {
  await load(); const check = deferred(); const calls = [];
  const view = render(app({ requestJson: async (url, options = {}) => { calls.push({ url, options }); return calls.length === 1 ? snapshot() : check.promise; } })); await ready(view);
  fireEvent.click(dateButton(view)); const detail = view.getByRole('dialog'); fireEvent.click(within(detail).getByRole('button', { name: /오후 가능/ }));
  fireEvent.click(within(detail).getByRole('button', { name: `${date} 오후 확인` }));
  assert.equal(calls.length, 2); assert.equal(dateButton(view).disabled, true); assert.equal(view.queryByRole('dialog') === null, true);
  assert.equal(view.getByTestId('location').textContent, '/bands/test-band/availability');
  await act(async () => check.resolve(snapshot()));
  await waitFor(() => assert.match(view.getByTestId('location').textContent, /\/rehearsals\?/));
  const next = new URL(view.getByTestId('location').textContent, 'http://localhost');
  assert.equal(next.searchParams.get('date'), date); assert.equal(next.searchParams.get('period'), 'afternoon'); assert.equal(next.searchParams.get('create'), '1'); assert.ok(next.searchParams.get('availability'));
  assert.equal(calls.some(({ options }) => options.method), false);
});

test('changed candidate preflight closes stale detail and reloads rather than navigating', async () => {
  await load(); let reads = 0; const view = render(app({ requestJson: async () => snapshot({ generation: reads++ === 0 ? 0 : 1 }) })); await ready(view);
  fireEvent.click(dateButton(view)); fireEvent.click(view.getByRole('button', { name: `${date} 저녁 확인` }));
  await view.findByText('가능 일정이 변경되었습니다. 최신 후보를 확인해 주세요.'); await waitFor(() => assert.equal(reads, 3));
  assert.equal(view.queryByRole('dialog') === null, true); assert.equal(view.getByTestId('location').textContent, '/bands/test-band/availability');
});
