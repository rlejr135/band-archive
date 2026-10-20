import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const user = userEvent.setup({ document: dom.window.document });
let vite; let removal; let auth;
const load = async () => { if (removal) return; vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); removal = await vite.ssrLoadModule('/src/components/bands/MemberRemoval.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); };
after(async () => { await vite?.close(); dom.window.close(); }); afterEach(() => cleanup());
const account = (id = 'owner', generation = 0) => ({ user: { id }, authGeneration: generation, getAccessToken: async () => id, refreshSession: async () => id, loading: false });
const target = { id: 42, band_id: 7, role: 'member', status: 'active', member_name: '<밴드 이름>', can_remove_from_band: true };
const band = { id: 7, name: '들뜬' };
const removedResult = { changed: true, membership: { id: 42, band_id: 7, status: 'left' } };
const button = (api, onRemoved = () => {}, membership = target, authValue = account()) => createElement(auth.AuthContext.Provider, { value: authValue }, createElement(removal.MemberRemovalButton, { api, band, membership, onRemoved }));
const platform = (authValue = account(), allowed = true) => createElement(auth.AuthContext.Provider, { value: authValue }, createElement(removal.PlatformMemberManagement, { allowed }));

test('removal requires strict server action permission, confirms the band-local name and preserves contents', async () => {
  await load(); const calls = []; const confirmations = []; const previous = window.confirm;
  window.confirm = (message) => { confirmations.push(message); return false; };
  const api = { requestJson: async (...args) => { calls.push(args); } };
  try {
    const view = render(button(api, undefined, { ...target, can_remove_from_band: 'true' })); assert.equal(view.queryByRole('button'), null);
    view.rerender(button(api)); fireEvent.click(view.getByRole('button', { name: '<밴드 이름> 추방' }));
    assert.equal(calls.length, 0); assert.match(confirmations[0], /들뜬/); assert.match(confirmations[0], /<밴드 이름>/); assert.match(confirmations[0], /콘텐츠와 멤버 프로필은 보존/); assert.match(confirmations[0], /다시 가입/); assert.equal(view.container.querySelector('밴드'), null);
  } finally { window.confirm = previous; }
});

test('double clicks send one scoped confirmation request and roster refresh failure is not removal failure', async () => {
  await load(); const previous = window.confirm; window.confirm = () => true;
  const pending = deferred(); const calls = []; const removed = [];
  try {
    const view = render(button({ requestJson: (...args) => { calls.push(args); return pending.promise; } }, (id) => { removed.push(id); throw new Error('read failed'); }));
    const action = view.getByRole('button'); fireEvent.click(action); fireEvent.click(action);
    assert.equal(calls.length, 1); assert.equal(calls[0][0], '/api/v2/bands/7/memberships/42/remove'); assert.equal(calls[0][1].method, 'POST'); assert.deepEqual(JSON.parse(calls[0][1].body), { confirm: true }); assert.equal(action.disabled, true);
    await act(async () => { pending.resolve(removedResult); });
    await view.findByText(/추방은 완료됐지만 최신 명단/); assert.deepEqual(removed, [42]); assert.equal(view.queryByRole('alert'), null); assert.equal(action.disabled, true); assert.equal(action.textContent, '추방 완료');
    fireEvent.click(action); assert.equal(calls.length, 1);
  } finally { window.confirm = previous; }
});

test('unknown mutation outcome blocks a blind repeat until roster is rechecked', async () => {
  await load(); const previous = window.confirm; window.confirm = () => true; let calls = 0;
  try {
    const view = render(button({ requestJson: async () => { calls += 1; throw new Error('connection lost'); } })); fireEvent.click(view.getByRole('button'));
    await view.findByRole('alert'); assert.match(view.getByRole('alert').textContent, /중복 요청하지 말고/); assert.equal(view.getByRole('button').disabled, true); fireEvent.click(view.getByRole('button')); assert.equal(calls, 1);
  } finally { window.confirm = previous; }
});

test('account generation switch aborts mutation and suppresses old success callbacks', async () => {
  await load(); const previous = window.confirm; window.confirm = () => true; const pending = deferred(); let signal; let callback = 0;
  const api = { requestJson: (_path, options) => { signal = options.signal; return pending.promise; } };
  try {
    const view = render(button(api, () => { callback += 1; })); fireEvent.click(view.getByRole('button'));
    view.rerender(button(api, () => { callback += 1; }, target, account('owner', 1))); assert.equal(signal.aborted, true);
    await act(async () => { pending.resolve(removedResult); }); assert.equal(callback, 0); assert.equal(view.getByRole('button').disabled, false);
  } finally { window.confirm = previous; }
});

test('platform management only reads minimal scoped roster and removes the selected member locally', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; window.confirm = () => true; const calls = [];
  globalThis.fetch = async (url, options = {}) => { calls.push([String(url), options]); return options.method === 'POST' ? response(removedResult) : response({ band, memberships: [target, { id: 3, band_id: 7, member_name: '소유자', status: 'active', role: 'owner', can_remove_from_band: false }] }); };
  try {
    const view = render(platform()); await user.type(view.getByLabelText('관리할 밴드 ID'), '7'); fireEvent.submit(view.getByRole('button', { name: '멤버 명단 조회' }).closest('form'));
    await view.findByText('들뜬 · ID 7'); assert.equal(view.queryByRole('button', { name: '소유자 추방' }), null);
    fireEvent.click(view.getByRole('button', { name: '<밴드 이름> 추방' })); await view.findByText(/멤버를 추방했습니다/); assert.equal(view.queryByText('<밴드 이름> · member'), null);
    assert.equal(calls.length, 2); assert.ok(calls[0][0].endsWith('/api/v2/platform/bands/7/memberships')); assert.ok(calls[1][0].endsWith('/api/v2/bands/7/memberships/42/remove')); assert.ok(calls.every(([path]) => !/invite|announcement|gallery|by-slug/.test(path)));
  } finally { globalThis.fetch = previousFetch; window.confirm = previousConfirm; }
});

test('platform band switch hides old roster synchronously and suppresses a late read', async () => {
  await load(); const previous = globalThis.fetch; const pending = deferred(); let signal;
  globalThis.fetch = async (url, options = {}) => { if (String(url).includes('/7/')) { signal = options.signal; return pending.promise; } return response({ band: { id: 8, name: '다른 밴드' }, memberships: [] }); };
  try {
    const view = render(platform()); const input = view.getByLabelText('관리할 밴드 ID'); await user.type(input, '7'); fireEvent.click(view.getByRole('button', { name: '멤버 명단 조회' })); await waitFor(() => assert.ok(signal));
    await user.clear(input); await user.type(input, '8'); assert.equal(signal.aborted, true); fireEvent.click(view.getByRole('button', { name: '멤버 명단 조회' })); await view.findByText('다른 밴드 · ID 8');
    await act(async () => { pending.resolve(response({ band, memberships: [target] })); }); assert.equal(view.queryByText('들뜬 · ID 7'), null);
  } finally { globalThis.fetch = previous; }
});

test('platform account change and removed permission never expose old roster or late mutations', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; window.confirm = () => true; const pending = deferred(); let mutationSignal;
  globalThis.fetch = async (_url, options = {}) => { if (options.method === 'POST') { mutationSignal = options.signal; return pending.promise; } return response({ band, memberships: [target] }); };
  try {
    const view = render(platform()); await user.type(view.getByLabelText('관리할 밴드 ID'), '7'); fireEvent.click(view.getByRole('button', { name: '멤버 명단 조회' })); await view.findByText('들뜬 · ID 7'); fireEvent.click(view.getByRole('button', { name: '<밴드 이름> 추방' })); await waitFor(() => assert.ok(mutationSignal));
    view.rerender(platform(account('different'))); assert.equal(view.queryByText('들뜬 · ID 7'), null); assert.equal(mutationSignal.aborted, true);
    await act(async () => { pending.resolve(response(removedResult)); }); assert.equal(view.queryByText(/멤버를 추방했습니다/), null);
    view.rerender(platform(account('different'), false)); assert.equal(view.queryByLabelText('관리할 밴드 ID'), null);
  } finally { globalThis.fetch = previousFetch; window.confirm = previousConfirm; }
});

test('malformed cross-band roster rows are rejected before any member name is rendered', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async () => response({ band, memberships: [{ ...target, band_id: 8, member_name: '다른 밴드 비공개 이름' }] });
  try {
    const view = render(platform()); await user.type(view.getByLabelText('관리할 밴드 ID'), '7'); fireEvent.click(view.getByRole('button', { name: '멤버 명단 조회' }));
    await view.findByRole('alert'); assert.equal(view.queryByText(/다른 밴드 비공개 이름/), null); assert.equal(view.queryByRole('button', { name: /추방$/ }), null);
  } finally { globalThis.fetch = previous; }
});

test('unknown result can be rechecked for the same band without repeating its mutation', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; window.confirm = () => true; let reads = 0; let writes = 0;
  globalThis.fetch = async (_url, options = {}) => { if (options.method === 'POST') { writes += 1; throw new Error('reply lost'); } reads += 1; return response({ band, memberships: reads === 1 ? [target] : [] }); };
  try {
    const view = render(platform()); await user.type(view.getByLabelText('관리할 밴드 ID'), '7'); fireEvent.click(view.getByRole('button', { name: '멤버 명단 조회' })); await view.findByText('들뜬 · ID 7');
    fireEvent.click(view.getByRole('button', { name: '<밴드 이름> 추방' })); await view.findByRole('alert'); assert.equal(view.getByRole('button', { name: '<밴드 이름> 추방' }).disabled, true);
    fireEvent.click(view.getByRole('button', { name: '명단 새로고침' })); await view.findByText('활성 멤버가 없습니다.'); assert.equal(writes, 1); assert.equal(reads, 2); assert.equal(view.queryByRole('alert'), null);
  } finally { globalThis.fetch = previousFetch; window.confirm = previousConfirm; }
});

test('mismatched removal response does not announce success or remove a roster row', async () => {
  await load(); const previous = window.confirm; window.confirm = () => true; let removed = false;
  try {
    const view = render(button({ requestJson: async () => ({ ...removedResult, membership: { id: 42, band_id: 8, status: 'left' } }) }, () => { removed = true; }));
    fireEvent.click(view.getByRole('button')); await view.findByRole('alert'); assert.equal(removed, false); assert.equal(view.queryByRole('status'), null); assert.equal(view.getByRole('button').disabled, true);
  } finally { window.confirm = previous; }
});
