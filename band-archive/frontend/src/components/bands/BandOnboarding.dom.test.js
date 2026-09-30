import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement, useEffect } from 'react';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, FormData: dom.window.FormData });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
let vite; let onboarding; let auth;
const load = async () => { if (onboarding) return; vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); onboarding = await vite.ssrLoadModule('/src/components/bands/BandOnboarding.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); };
after(async () => { await vite?.close(); dom.window.close(); }); afterEach(() => cleanup());
const account = (id = 'u1') => ({ user: id ? { id } : null, getAccessToken: async () => id, refreshSession: async () => id, loading: false });
const Login = () => createElement('p', null, `login:${useLocation().search}`);
const Go = ({ target }) => { const navigate = useNavigate(); useEffect(() => { navigate(target); }, [navigate, target]); return null; };
const App = ({ entry, authValue = account(), manage = false, discover = false, create = false }) => createElement(auth.AuthContext.Provider, { value: authValue }, createElement(MemoryRouter, { initialEntries: [entry] }, manage && createElement(Go, { target: entry }), createElement(Routes, null,
  discover ? createElement(Route, { path: '/bands/discover', element: createElement(onboarding.DiscoverBandsPage) }) : null,
  create ? createElement(Route, { path: '/bands/request', element: createElement(onboarding.BandCreationRequestPage) }) : null,
  manage ? createElement(Route, { path: '/bands/:bandSlug/manage', element: createElement(onboarding.BandManageRoute) }) : null,
  createElement(Route, { path: '/login', element: createElement(Login) }))));

test('visitor discover preserves a safe login return and join becomes pending once', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, request = {}) => { calls.push([String(url), request]); return response({ bands: [{ id: 7, slug: 'public', name: 'Public' }], pagination: { total: 1, has_more: false } }); };
  try { const visitor = render(createElement(App, { entry: '/bands/discover', authValue: account(null), discover: true })); await visitor.findByText('Public'); await userEvent.click(visitor.getByRole('button', { name: '로그인 후 가입 요청' })); await visitor.findByText('login:?returnTo=%2Fbands%2Fdiscover'); assert.ok(calls.every(([url]) => !url.includes('invite'))); visitor.unmount();
    let joined = 0; globalThis.fetch = async (url) => { if (String(url).endsWith('/api/v2/bands')) return response({ bands: [], pending_memberships: [] }); if (String(url).includes('join-requests')) { joined += 1; return response({ membership: { id: 1 }, created: true }, 201); } return response({ bands: [{ id: 7, slug: 'public', name: 'Public' }], pagination: { total: 1, has_more: false } }); };
    const member = render(createElement(App, { entry: '/bands/discover', discover: true })); await member.findByText('Public'); const join = member.getByRole('button', { name: '가입 요청' }); await userEvent.click(join); await waitFor(() => assert.equal(joined, 1)); await member.findByRole('button', { name: '승인 대기 중' }); await userEvent.click(member.getByRole('button', { name: '승인 대기 중' })); assert.equal(joined, 1);
  } finally { globalThis.fetch = previous; }
});

test('invite code stays in a POST body, clears the input, and has a generic invalid result', async () => {
  await load(); const previous = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => { writes.push([String(url), options]); return response({}, 404); };
  try { const view = render(createElement(auth.AuthContext.Provider, { value: account() }, createElement(onboarding.InviteCodeEntry))); const input = view.getByLabelText('초대 코드'); await userEvent.type(input, 'secret-capability'); await userEvent.click(view.getByRole('button', { name: '가입' })); await view.findByText('초대 코드를 사용할 수 없습니다.'); assert.equal(input.value, ''); assert.equal(writes.length, 1); assert.ok(writes[0][0].endsWith('/api/v2/invite-codes/join')); assert.equal(writes[0][0].includes('secret-capability'), false); assert.deepEqual(JSON.parse(writes[0][1].body), { code: 'secret-capability' }); } finally { globalThis.fetch = previous; }
});

test('creation form has one in-flight submit and reports its status', async () => {
  await load(); const previous = globalThis.fetch; const first = deferred(); let writes = 0;
  globalThis.fetch = async (url) => { if (String(url).includes('band-creation-requests')) { writes += 1; return first.promise; } throw new Error('unexpected'); };
  try { const view = render(createElement(App, { entry: '/bands/request', create: true })); await userEvent.type(view.getByLabelText('밴드 이름'), '새 밴드'); await userEvent.type(view.getByLabelText('주소(slug)'), 'new-band'); const button = view.getByRole('button', { name: '생성 요청 보내기' }); await userEvent.click(button); await userEvent.click(button); assert.equal(writes, 1); await act(async () => first.resolve(response({ request: { name: '새 밴드', status: 'pending' } }, 201))); await view.findByText(/승인 대기 중/); } finally { globalThis.fetch = previous; }
});

test('management hides non-manager and ignores a late A membership decision after route switch', async () => {
  await load(); const previous = globalThis.fetch; const decision = deferred(); const aDetail = { band: { id: 1, slug: 'a', name: 'A', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } }; const bDetail = { band: { id: 2, slug: 'b', name: 'B', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } };
  globalThis.fetch = async (url, options = {}) => { const value = String(url); if (value.includes('/by-slug/a')) return response(aDetail); if (value.includes('/by-slug/b')) return response(bDetail); if (value.includes('/bands/1/memberships')) return response({ memberships: [{ id: 12, status: 'pending', user: { display_name: 'A 요청' } }] }); if (value.includes('/bands/2/memberships')) return response({ memberships: [] }); if (value.includes('/invite-codes') && options.method !== 'POST') return response({ invite_codes: [] }); if (value.includes('/memberships/12')) return decision.promise; throw new Error(`unexpected ${value}`); };
  try { const view = render(createElement(App, { entry: '/bands/a/manage', manage: true })); await view.findByText('A 요청'); await userEvent.click(view.getByRole('button', { name: '승인' })); view.rerender(createElement(App, { entry: '/bands/b/manage', manage: true })); await view.findByText('B 관리'); await act(async () => decision.resolve(response({ membership: { id: 12 } }))); await new Promise((done) => setTimeout(done, 0)); assert.equal(view.queryByText('A 요청'), null); assert.equal(view.getByText('B 관리').textContent, 'B 관리'); view.unmount();
    globalThis.fetch = async (url) => String(url).includes('/by-slug/member') ? response({ band: { id: 4, slug: 'member', permissions: { can_manage_members: false, can_manage_invites: false } } }) : response({}); const denied = render(createElement(App, { entry: '/bands/member/manage', manage: true })); await denied.findByText('찾을 수 없는 관리 공간입니다.'); assert.equal(denied.queryByText('초대 코드'), null);
  } finally { globalThis.fetch = previous; }
});
