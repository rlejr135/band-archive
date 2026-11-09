import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';
import { monthDays, seoulToday } from './availabilityState.js';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
const { act, cleanup, fireEvent, render, waitFor, within } = await import('@testing-library/react');
const { default: userEvent } = await import('@testing-library/user-event');
const { createElement } = await import('react');
const { MemoryRouter } = await import('react-router-dom');
dom.window.PointerEvent = class extends dom.window.MouseEvent { constructor(type, options = {}) { super(type, options); Object.defineProperty(this, 'pointerType', { configurable: true, value: options.pointerType || 'mouse' }); } };
let vite; let Calendar;
const dates = monthDays(seoulToday().slice(0, 7)); const date = dates[0];
const snapshot = (overrides = {}) => ({ band_id: 8, generation: 0, last_reset_at: null, can_reset: false, reset_summary: { days: 1, notes: 0 }, members: [{ membership_id: 12, member_id: 777, display_name: '밴드 베이스' }, { membership_id: 13, member_id: 12, display_name: '밴드 보컬' }], days: [{ membership_id: 12, date, morning: 'UNAVAILABLE', afternoon: 'UNKNOWN', evening: 'AVAILABLE', revision: 3 }], notes: [], my_membership_id: 12, ...overrides });
const band = { id: 8, slug: 'test-band', name: '테스트 밴드', membership: { id: 12, status: 'active', role: 'member' } };
const load = async () => { if (Calendar) return; vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); Calendar = (await vite.ssrLoadModule('/src/components/workspace/AvailabilityCalendar.jsx')).default; };
const app = (api, props = {}) => createElement(MemoryRouter, null, createElement(Calendar, { band, api, workspaceKey: '8:user', ...props }));
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const slot = (view, day = date, period = 'morning') => view.container.querySelector(`[data-availability-slot][data-date="${day}"][data-period="${period}"]`);
const ready = async (view) => waitFor(() => assert.equal(Boolean(slot(view)), true));
afterEach(() => cleanup()); after(async () => { await vite?.close(); dom.window.close(); });

test('Sunday-first view aggregates all active members and popovers use band names without writes or reads', async () => {
  await load(); const calls = []; const view = render(app({ requestJson: async (_url, options = {}) => { calls.push(options); return snapshot(); } })); await ready(view);
  const modes = view.getByRole('group', { name: '달력 모드' });
  const calendarCard = view.container.querySelector('.availability-panel[aria-labelledby="calendar-availability-title"]');
  assert.equal(calendarCard.firstElementChild === modes, true);
  assert.equal(modes.nextElementSibling === view.getByRole('heading', { name: '멤버 가능 시간' }), true);
  assert.equal(view.getAllByRole('group', { name: '달력 모드' }).length, 1);
  assert.equal(view.container.querySelector('.availability-overview-grid') !== null, true);
  assert.equal(view.container.querySelector('.availability-editor-grid') === null, true);
  assert.deepEqual([...view.container.querySelectorAll('thead th')].map((th) => th.textContent), ['일', '월', '화', '수', '목', '금', '토']);
  assert.equal(view.container.querySelectorAll('[data-availability-slot]').length, dates.length * 3);
  assert.equal(view.container.querySelectorAll('.availability-empty-day button').length, 0);
  assert.equal(slot(view).textContent, '오전X'); assert.equal(slot(view, date, 'evening').textContent, '저녁△');
  assert.equal(view.container.querySelectorAll('.availability-slot-button.is-dirty').length, 0);
  assert.match(slot(view).getAttribute('aria-label'), /전원 불가능/); assert.match(slot(view, date, 'evening').getAttribute('aria-label'), /일부 가능·일부 불가능/);
  fireEvent.pointerEnter(slot(view, date, 'evening').parentElement, { pointerType: 'mouse' });
  const tip = view.getByRole('tooltip'); assert.ok(within(tip).getByText('밴드 베이스 (나)')); assert.ok(within(tip).getByText('밴드 보컬'));
  assert.ok(within(tip).getByText('O 가능')); assert.ok(within(tip).getByText('X 불가능'));
  fireEvent.pointerLeave(slot(view, date, 'evening').parentElement, { pointerType: 'mouse' }); assert.equal(view.queryByRole('tooltip') === null, true);
  fireEvent.focus(slot(view)); assert.ok(view.getByRole('tooltip')); fireEvent.keyDown(document, { key: 'Escape' }); assert.equal(view.queryByRole('tooltip') === null, true);
  fireEvent.click(view.getByText('후보 계산 대상', { selector: 'summary' }));
  const picks = view.getByRole('group', { name: '후보 계산 대상' }); fireEvent.click(within(picks).getByRole('checkbox', { name: '밴드 보컬' }));
  assert.equal(slot(view, date, 'evening').textContent, '저녁△'); assert.equal(calls.length, 1); assert.equal(calls.filter((options) => options.method).length, 0);
});

test('touch pin toggles, closes explicitly or outside, and clears on mode and month changes', async () => {
  await load(); const api = { requestJson: async (url) => { const from = new URL(url, 'http://localhost').searchParams.get('from'); return snapshot({ days: from === date ? snapshot().days : [] }); } };
  const view = render(app(api)); await ready(view);
  fireEvent.click(slot(view)); assert.ok(view.getByRole('dialog')); fireEvent.click(slot(view)); assert.equal(view.queryByRole('dialog') === null, true);
  fireEvent.click(slot(view)); const close = view.getByRole('button', { name: '선택 내역 닫기' }); close.focus(); fireEvent.click(close);
  assert.equal(document.activeElement === slot(view), true); assert.equal(view.queryByRole('dialog') === null && view.queryByRole('tooltip') === null, true);
  fireEvent.click(slot(view)); const escapeFrom = view.getByRole('button', { name: '선택 내역 닫기' }); escapeFrom.focus(); fireEvent.keyDown(escapeFrom, { key: 'Escape' });
  assert.equal(document.activeElement === slot(view), true); assert.equal(view.queryByRole('dialog') === null && view.queryByRole('tooltip') === null, true);
  fireEvent.click(slot(view)); const outside = view.getByRole('button', { name: '다음 달' }); outside.focus(); fireEvent.pointerDown(outside);
  assert.equal(view.queryByRole('dialog') === null, true); assert.equal(document.activeElement === outside, true);
  fireEvent.click(slot(view)); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); assert.equal(view.queryByRole('dialog') === null, true);
  assert.equal(view.container.querySelector('.availability-editor-grid') !== null, true);
  fireEvent.click(view.getByRole('button', { name: '멤버 선택 여부 보기' })); fireEvent.click(slot(view)); fireEvent.click(view.getByRole('button', { name: '다음 달' }));
  await waitFor(() => assert.equal(view.queryByRole('dialog') === null, true));
});

test('an empty active roster has no false all-unavailable aggregate', async () => {
  await load(); const view = render(app({ requestJson: async () => snapshot({ members: [], days: [] }) })); await ready(view);
  assert.equal(slot(view).textContent, '오전—'); assert.match(slot(view).getAttribute('aria-label'), /집계할 활성 멤버 없음/);
  fireEvent.click(slot(view)); assert.ok(within(view.getByRole('dialog')).getByText('활성 멤버가 없습니다.'));
});

test('draft clicks only preview locally, revert cleanly and batch multiple changed dates on explicit save', async () => {
  await load(); const write = deferred(); const confirm = deferred(); const calls = []; let gets = 0;
  const api = { requestJson: async (url, options = {}) => { calls.push({ url, options }); if (options.method) return write.promise; return gets++ === 0 ? snapshot() : confirm.promise; } };
  const view = render(app(api)); await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' }));
  const save = () => view.getByRole('button', { name: '저장', exact: true });
  assert.equal(slot(view, dates[1], 'morning').textContent, '오전O');
  assert.equal(slot(view, dates[1], 'afternoon').textContent, '오후O');
  assert.equal(slot(view, dates[1], 'evening').textContent, '저녁O');
  assert.equal(view.container.querySelectorAll('.availability-slot-button.is-dirty').length, 0);
  assert.equal(calls.length, 1);
  assert.equal(save().disabled, true); fireEvent.click(slot(view)); assert.equal(slot(view).textContent, '오전O'); assert.equal(save().disabled, false);
  assert.equal(slot(view).classList.contains('is-dirty'), true); assert.match(slot(view).getAttribute('aria-label'), /저장 전 변경/);
  assert.equal(slot(view).querySelector('.availability-dirty-marker') !== null, true);
  assert.equal(slot(view, date, 'afternoon').classList.contains('is-dirty'), false); assert.equal(slot(view, date, 'evening').classList.contains('is-dirty'), false);
  fireEvent.click(slot(view, date, 'evening')); assert.equal(slot(view, date, 'evening').textContent, '저녁X'); assert.equal(slot(view, date, 'evening').classList.contains('is-dirty'), true);
  fireEvent.click(slot(view, date, 'evening')); assert.equal(slot(view, date, 'evening').classList.contains('is-dirty'), false); assert.equal(slot(view).classList.contains('is-dirty'), true);
  assert.equal(calls.length, 1); fireEvent.click(slot(view)); assert.equal(slot(view).textContent, '오전X'); assert.equal(save().disabled, true);
  assert.equal(slot(view).classList.contains('is-dirty'), false); assert.doesNotMatch(slot(view).getAttribute('aria-label'), /저장 전 변경/);
  fireEvent.click(slot(view)); fireEvent.click(slot(view, dates[1], 'evening')); assert.equal(calls.length, 1);
  const saveButton = save(); fireEvent.click(saveButton); fireEvent.click(saveButton); assert.equal(calls.filter(({ options }) => options.method).length, 1);
  const sent = calls.find(({ options }) => options.method); assert.match(sent.url, /\/me\/batch$/);
  const expected = [{ date, revision: 3, morning: 'AVAILABLE', afternoon: 'UNAVAILABLE', evening: 'AVAILABLE' }, { date: dates[1], revision: 0, morning: 'AVAILABLE', afternoon: 'AVAILABLE', evening: 'UNAVAILABLE' }];
  assert.deepEqual(JSON.parse(sent.options.body), { generation: 0, days: expected });
  assert.equal(slot(view).getAttribute('aria-busy'), 'true'); assert.equal(view.getByRole('button', { name: '다음 달' }).disabled, true);
  await act(async () => write.resolve({ changed: true, generation: 0 })); await waitFor(() => assert.equal(gets, 2));
  assert.ok(view.container.querySelector('table')); assert.equal(slot(view).disabled, true);
  await act(async () => confirm.resolve(snapshot({ days: expected.map((row) => ({ ...row, membership_id: 12, revision: row.revision + 1 })) })));
  await waitFor(() => assert.equal(slot(view).disabled, false)); assert.equal(slot(view).textContent, '오전O'); assert.equal(save().disabled, true);
  assert.equal(view.container.querySelectorAll('.availability-slot-button.is-dirty').length, 0);
});
for (const status of [409, 500]) test(`failed toggle ${status} reloads latest without automatically repeating PUT`, async () => {
  await load(); let gets = 0; let writes = 0;
  const view = render(app({ requestJson: async (_url, options = {}) => { if (options.method) { writes += 1; const error = new Error('failed'); error.status = status; throw error; } return snapshot({ generation: gets++ ? 7 : 0 }); } }));
  await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); fireEvent.click(slot(view)); fireEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  await waitFor(() => assert.equal(gets, 2)); await waitFor(() => assert.equal(slot(view).disabled, false)); assert.equal(writes, 1);
  assert.ok(view.getByText(/다른 기기에서 수정했거나/)); assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true);
  assert.equal(view.container.querySelectorAll('.availability-slot-button.is-dirty').length, 0);
});

test('auth loss during toggle hides schedule data and does not replay a write', async () => {
  await load(); let writes = 0;
  const view = render(app({ requestJson: async (_url, options = {}) => { if (options.method) { writes += 1; const error = new Error('forbidden'); error.status = 403; throw error; } return snapshot(); } }));
  await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); fireEvent.click(slot(view)); fireEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  await view.findByRole('alert'); assert.equal(view.container.querySelector('table') === null, true); assert.equal(view.queryByText('밴드 보컬') === null, true); assert.equal(writes, 1);
});

test('a failed confirming GET hides stale data until manual reload and never repeats PUT', async () => {
  await load(); let gets = 0; let writes = 0;
  const api = { requestJson: async (_url, options = {}) => { if (options.method) { writes += 1; return {}; } gets += 1; if (gets === 2) throw new Error('GET unavailable'); return snapshot(); } };
  const view = render(app(api)); await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); fireEvent.click(slot(view)); fireEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  await view.findByRole('alert'); assert.equal(view.container.querySelector('table') === null, true); assert.equal(writes, 1);
  fireEvent.click(view.getByRole('button', { name: '다시 시도', exact: true })); await ready(view); assert.equal(writes, 1); assert.equal(gets, 3);
});

test('network failure preserves a verifiably unchanged draft for manual retry only', async () => {
  await load(); let writes = 0; let gets = 0;
  const view = render(app({ requestJson: async (_url, options = {}) => { if (options.method) { writes += 1; throw new TypeError('connection lost'); } gets += 1; return snapshot(); } }));
  await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); fireEvent.click(slot(view));
  fireEvent.click(view.getByRole('button', { name: '저장', exact: true })); await view.findByText(/초안을 유지했습니다/);
  assert.equal(writes, 1); assert.equal(gets, 2); assert.equal(slot(view).textContent, '오전O'); assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, false);
  assert.equal(slot(view).classList.contains('is-dirty'), true);
});

test('ambiguous PUT plus unavailable GET stays locked until read-only verification confirms the submitted values', async () => {
  await load(); let gets = 0; let writes = 0;
  const api = { requestJson: async (_url, options = {}) => { if (options.method) { writes += 1; throw new TypeError('response lost'); } gets += 1; if (gets === 2) throw new TypeError('GET failed'); return gets === 1 ? snapshot() : snapshot({ days: [{ ...snapshot().days[0], morning: 'AVAILABLE', revision: 4 }] }); } };
  const view = render(app(api)); await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); fireEvent.click(slot(view)); fireEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  await view.findByRole('alert'); assert.equal(view.queryByRole('button', { name: '저장', exact: true }) === null, true); assert.equal(writes, 1);
  fireEvent.click(view.getByRole('button', { name: '다시 시도', exact: true })); await ready(view);
  assert.equal(slot(view).textContent, '오전O'); assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true); assert.equal(writes, 1); assert.equal(gets, 3);
});

test('API identity refresh cannot silently rebase a dirty draft onto a new reset generation', async () => {
  await load(); let writes = 0; const api = { requestJson: async () => snapshot() };
  const view = render(app(api)); await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); fireEvent.click(slot(view));
  view.rerender(app({ requestJson: async (_url, options = {}) => { if (options.method) writes += 1; return snapshot({ generation: 9 }); } }));
  await view.findByText(/최신 상태를 복구했으니 다시 선택/); await ready(view);
  assert.equal(slot(view).textContent, '오전X'); assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true); assert.equal(writes, 0);
});

test('discard guards protect navigation while independent note saving preserves availability drafts', async () => {
  await load(); const calls = []; const previous = window.confirm; window.confirm = () => false;
  try {
    const view = render(app({ requestJson: async (_url, options = {}) => { calls.push(options); return snapshot(); } })); await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); fireEvent.click(slot(view));
    const beforeUnload = new window.Event('beforeunload', { cancelable: true }); window.dispatchEvent(beforeUnload); assert.equal(beforeUnload.defaultPrevented, true);
    fireEvent.click(view.getByRole('button', { name: '멤버 선택 여부 보기' })); fireEvent.click(view.getByRole('button', { name: '다음 달' })); fireEvent.click(view.getByRole('button', { name: '새로고침' })); fireEvent.click(view.getByRole('link', { name: '확정 일정' }));
    assert.equal(view.getByRole('button', { name: '내 일정 선택하기' }).getAttribute('aria-pressed'), 'true'); assert.equal(slot(view).textContent, '오전O'); assert.equal(calls.length, 1);
    fireEvent.click(view.getByText('선택 개인 일정 메모')); const user = userEvent.setup({ document: dom.window.document }); await user.type(view.getByLabelText('개인 일정 (선택)'), '메모 초안'); fireEvent.click(view.getByRole('button', { name: '메모 저장' }));
    await waitFor(() => assert.equal(calls.length, 3)); await waitFor(() => assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, false));
    assert.equal(slot(view).textContent, '오전O');
    assert.equal(calls.filter((options) => options.method && JSON.parse(options.body).days).length, 0);
    assert.equal(calls.filter((options) => options.method && JSON.parse(options.body).body === '메모 초안').length, 1);
    window.confirm = () => true; fireEvent.click(view.getByRole('button', { name: '변경 취소' }));
    assert.equal(slot(view).textContent, '오전X'); assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true);
    assert.equal(view.container.querySelectorAll('.availability-slot-button.is-dirty').length, 0);
    const cleanUnload = new window.Event('beforeunload', { cancelable: true }); window.dispatchEvent(cleanUnload); assert.equal(cleanUnload.defaultPrevented, false);
  } finally { window.confirm = previous; }
});

test('candidate details stay persisted and cancelling a candidate transition never writes an availability draft', async () => {
  await load(); let reads = 0; let writes = 0; const previous = window.confirm; window.confirm = () => false; const today = seoulToday();
  try {
    const view = render(app({ requestJson: async (_url, options = {}) => { if (options.method) writes += 1; else reads += 1; return snapshot({ days: [{ ...snapshot().days[0], date: today }] }); } }, { band: { ...band, membership: { ...band.membership, role: 'owner' } } }));
    await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' }));
    fireEvent.click(slot(view, today, 'afternoon')); assert.equal(slot(view, today, 'afternoon').textContent, '오후O');
    fireEvent.click(view.getByRole('button', { name: `${today} 후보 시간대별 멤버 가능 여부 보기` }));
    const detail = view.getByRole('dialog', { name: `${today} 후보 상세` }); fireEvent.click(within(detail).getByRole('button', { name: /오후 가능/ }));
    assert.equal(within(detail).getAllByText('X 불가능').length, 2);
    fireEvent.click(within(detail).getByRole('button', { name: `${today} 오후 확인` }));
    assert.equal(reads, 1); assert.equal(writes, 0); assert.equal(slot(view, today, 'afternoon').textContent, '오후O');
    assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, false);
  } finally { window.confirm = previous; }
});

test('cancelling a reset discard confirmation preserves dirty days and never resets remotely', async () => {
  await load(); let writes = 0; const previous = window.confirm; let confirms = 0; window.confirm = () => ++confirms === 1;
  try {
    const view = render(app({ requestJson: async (_url, options = {}) => { if (options.method) writes += 1; return snapshot({ can_reset: true }); } }));
    await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); fireEvent.click(slot(view));
    fireEvent.click(view.getByRole('button', { name: '이번 달 내 상태 초기화' }));
    assert.equal(confirms, 2); assert.equal(writes, 0); assert.equal(slot(view).textContent, '오전O'); assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, false);
  } finally { window.confirm = previous; }
});

test('malformed existing day fields are rejected instead of silently overwriting another period', async () => {
  await load(); let writes = 0;
  const view = render(app({ requestJson: async (_url, options = {}) => { if (options.method) writes += 1; return snapshot({ days: [{ ...snapshot().days[0], afternoon: undefined }] }); } }));
  await view.findByRole('alert'); assert.equal(view.container.querySelector('table') === null, true); assert.equal(writes, 0);
});

test('notes remain opt-in plain text and preserve tombstone revision and profile identity', async () => {
  await load(); const writes = [];
  const view = render(app({ requestJson: async (_url, options = {}) => { if (options.method) writes.push(options); return options.method ? {} : snapshot({ notes: [{ membership_id: 12, date, period: 'evening', revision: 7, body: null, visibility: 'PRIVATE' }, { membership_id: 13, date, period: 'evening', revision: 1, body: '<img onerror=alert(1)>', visibility: 'BAND' }] }); } }));
  await ready(view); fireEvent.click(slot(view)); assert.equal(writes.length, 0); fireEvent.click(view.getByText('선택 개인 일정 메모'));
  assert.equal(view.getByLabelText('이 메모를 같은 밴드 멤버에게 공유').checked, false);
  const user = userEvent.setup({ document: dom.window.document }); await user.type(view.getByLabelText('개인 일정 (선택)'), '잠시 일정');
  assert.equal(writes.length, 0); fireEvent.click(view.getByRole('button', { name: '메모 저장' })); await waitFor(() => assert.equal(writes.length, 1));
  assert.deepEqual(JSON.parse(writes[0].body), { generation: 0, revision: 7, body: '잠시 일정', visibility: 'PRIVATE' }); assert.equal(view.container.querySelector('img') === null, true);
  assert.equal(view.container.querySelector('.availability-shared-notes strong').textContent, '밴드 보컬 · 저녁');
});

test('self and owner resets remain explicit and use generation without changing notes or confirmed rehearsals', async () => {
  await load(); const writes = []; const previous = window.confirm; window.confirm = () => true;
  try {
    const view = render(app({ requestJson: async (url, options = {}) => { if (options.method) writes.push({ url, body: JSON.parse(options.body) }); return options.method ? {} : snapshot({ can_reset: true }); } })); await ready(view);
    fireEvent.click(view.getByRole('button', { name: '이번 달 내 상태 초기화' })); await waitFor(() => assert.equal(writes.length, 1));
    assert.equal(writes[0].body.days.length, dates.length); assert.equal(writes[0].body.days.every((day) => day.morning === 'UNAVAILABLE' && day.afternoon === 'UNAVAILABLE' && day.evening === 'UNAVAILABLE'), true);
    await waitFor(() => assert.equal(view.getByRole('button', { name: '모두의 조율 일정 초기화' }).disabled, false)); fireEvent.click(view.getByRole('button', { name: '모두의 조율 일정 초기화' }));
    assert.ok(view.getByRole('alertdialog')); assert.equal(writes.length, 1); fireEvent.click(view.getByRole('button', { name: '확인하고 전체 초기화' })); await waitFor(() => assert.equal(writes.length, 2));
    assert.deepEqual(writes[1].body, { generation: 0, confirm: true });
  } finally { window.confirm = previous; }
});

test('account change aborts a pending toggle and ignores its late completion', async () => {
  await load(); const write = deferred(); let signal;
  const api = { requestJson: async (_url, options = {}) => { if (options.method) { signal = options.signal; return write.promise; } return snapshot(); } };
  const view = render(app(api)); await ready(view); fireEvent.click(view.getByRole('button', { name: '내 일정 선택하기' })); fireEvent.click(slot(view)); fireEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  view.rerender(app(api, { workspaceKey: '8:other-user', band: { ...band, membership: null } })); assert.equal(signal.aborted, true);
  await act(async () => write.resolve({ changed: true })); assert.equal(view.container.querySelector('table') === null, true);
});

test('old band GET cannot replace the next band, and inactive membership never requests data', async () => {
  await load(); const old = deferred(); let oldSignal;
  const api = { requestJson: async (url, options = {}) => { if (url.includes('/8/')) { oldSignal = options.signal; return old.promise; } return snapshot({ band_id: 9, members: [{ membership_id: 14, display_name: '새 밴드 멤버' }], my_membership_id: 14, days: [] }); } };
  const view = render(app(api)); await waitFor(() => assert.ok(oldSignal)); view.rerender(app(api, { band: { ...band, id: 9, slug: 'next' }, workspaceKey: '9:user' })); fireEvent.click(await view.findByText('후보 계산 대상', { selector: 'summary' })); await view.findByRole('checkbox', { name: '새 밴드 멤버' }); assert.equal(oldSignal.aborted, true);
  await act(async () => old.resolve(snapshot())); assert.equal(view.queryByText('밴드 보컬') === null, true);
  let calls = 0; view.rerender(app({ requestJson: async () => { calls += 1; } }, { band: { ...band, membership: null }, workspaceKey: 'none' })); assert.equal(calls, 0);
});
