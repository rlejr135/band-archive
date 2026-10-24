import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement, StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, FormData: dom.window.FormData });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
let vite; let auth; let bands; let page; let admin; let upload;
const load = async () => {
  if (page) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
  page = await vite.ssrLoadModule('/src/components/bands/BandsPage.jsx');
  admin = await vite.ssrLoadModule('/src/components/bands/BandCreationRequestsAdmin.jsx');
  upload = await vite.ssrLoadModule('/src/context/UploadContext.jsx');
};
after(async () => { await vite?.close(); dom.window.close(); }); afterEach(() => cleanup());

test('account hub reads memberships once and renders platform review link only from server privilege', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return String(url).includes('/api/v2/me') ? response({ user: { is_platform_admin: false } }) : response({ bands: [], pending_memberships: [] }); };
  const authValue = { user: { id: 'member-1', user_metadata: { is_platform_admin: true }, email: 'admin@example.test' }, getAccessToken: async () => 'member-token', refreshSession: async () => 'member-token', loading: false, signOut: async () => {} };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue },
      createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage)))));
    await view.findByText('내 밴드');
    assert.equal(calls.filter((url) => url.endsWith('/api/v2/bands')).length, 1);
    assert.ok(calls.some((url) => url.endsWith('/api/v2/me')));
    assert.equal(view.queryByRole('link', { name: '밴드 생성 요청 관리' }), null);
    assert.equal(view.queryByText(/소유권/), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('settings account and band management anchors focus and scroll to the existing sections on the same account page', async () => {
  await load();
  const previousFetch = globalThis.fetch;
  const previousScroll = dom.window.HTMLElement.prototype.scrollIntoView;
  const scrolled = [];
  dom.window.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this.id); };
  globalThis.fetch = async () => response({ user: { is_platform_admin: false } });
  const authValue = { user: { id: 'anchor-user' }, getAccessToken: async () => 'synthetic-token', refreshSession: async () => 'synthetic-token', loading: false, signOut: async () => {} };
  const bandValue = { activeBands: [], pendingMemberships: [], selectedBand: null, selectBand: () => {}, loading: false, error: '', reload: () => {} };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: bandValue }, createElement(MemoryRouter, { initialEntries: ['/account#profile-heading'] }, createElement(page.BandsPage)))));
    await waitFor(() => assert.equal(document.activeElement?.id, 'profile-heading'));
    assert.equal(scrolled.at(-1), 'profile-heading');
    await userEvent.click(view.getByRole('button', { name: '설정' }));
    await userEvent.click(view.getByRole('link', { name: '밴드 관리', exact: true }));
    await waitFor(() => assert.equal(document.activeElement?.id, 'memberships-heading'));
    assert.equal(scrolled.at(-1), 'memberships-heading');
    assert.equal(view.queryByRole('button', { name: '로그아웃' }), null);
    await userEvent.click(view.getByRole('button', { name: '설정' }));
    await userEvent.click(view.getByRole('link', { name: '계정', exact: true }));
    await waitFor(() => assert.equal(document.activeElement?.id, 'profile-heading'));
    assert.equal(scrolled.at(-1), 'profile-heading');
  } finally {
    globalThis.fetch = previousFetch;
    if (previousScroll) dom.window.HTMLElement.prototype.scrollIntoView = previousScroll;
    else delete dom.window.HTMLElement.prototype.scrollIntoView;
  }
});

test('the band management anchor waits for memberships to finish loading before moving focus', async () => {
  await load();
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => response({ user: { is_platform_admin: false } });
  const authValue = { user: { id: 'loading-anchor-user' }, getAccessToken: async () => 'synthetic-token', refreshSession: async () => 'synthetic-token', loading: false, signOut: async () => {} };
  const tree = (loading) => createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: { activeBands: [], pendingMemberships: [], selectedBand: null, selectBand: () => {}, loading, error: '', reload: () => {} } }, createElement(MemoryRouter, { initialEntries: ['/account#memberships-heading'] }, createElement(page.BandsPage))));
  try {
    const view = render(tree(true));
    assert.notEqual(document.activeElement?.id, 'memberships-heading');
    view.rerender(tree(false));
    await waitFor(() => assert.equal(document.activeElement?.id, 'memberships-heading'));
  } finally { globalThis.fetch = previousFetch; }
});

test('account hub shows the superadmin label and management link only for a strict server true', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => String(url).endsWith('/api/v2/me') ? response({ user: { is_platform_admin: true } }) : response({ bands: [], pending_memberships: [] });
  const authValue = { user: { id: 'verified-admin', user_metadata: { is_platform_admin: false } }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage)))));
    await view.findByRole('link', { name: '밴드 생성 요청 관리' });
    assert.ok(view.getByText('Superadmin'));
  } finally { globalThis.fetch = previousFetch; }
});

test('account switch hides prior pending memberships and ignores a late response from the old account', async () => {
  await load(); const previousFetch = globalThis.fetch; const accountA = deferred(); const accountB = deferred(); const calls = [];
  globalThis.fetch = async (url) => { if (String(url).endsWith('/api/v2/me')) return response({ user: { is_platform_admin: false } }); calls.push(String(url)); return calls.length === 1 ? accountA.promise : accountB.promise; };
  const renderAccount = (id) => createElement(auth.AuthContext.Provider, { value: { user: { id }, getAccessToken: async () => id, refreshSession: async () => id, loading: false, signOut: async () => {} } },
    createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage))));
  try {
    const view = render(renderAccount('account-a'));
    await waitFor(() => assert.equal(calls.length, 1));
    view.rerender(renderAccount('account-b'));
    await waitFor(() => assert.equal(calls.length, 2));
    await act(async () => accountA.resolve(response({ bands: [], pending_memberships: [{ id: 1, band: { name: 'A private pending band' }, requested_positions: ['OTHER'], requested_custom_position: 'A private position' }] })));
    assert.equal(view.queryByText('A private pending band'), null);
    await act(async () => accountB.resolve(response({ bands: [], pending_memberships: [{ id: 2, band: { name: 'B pending band' }, requested_positions: ['OTHER'], requested_custom_position: '색소폰' }] })));
    await view.findByText('B pending band', { exact: false });
    assert.ok(view.getByText('요청 포지션: 색소폰'));
    assert.equal(view.queryByText('A private position'), null);
    assert.equal(view.queryByText('A private pending band'), null);
    assert.equal(calls.length, 2);
  } finally { globalThis.fetch = previousFetch; }
});

test('already-loaded account memberships disappear synchronously while the next account is still loading', async () => {
  await load(); const previousFetch = globalThis.fetch; const accountB = deferred(); const calls = [];
  globalThis.fetch = async (url) => { if (String(url).endsWith('/api/v2/me')) return response({ user: { is_platform_admin: false } }); calls.push(calls.length + 1); return calls.length === 1
    ? response({ bands: [], pending_memberships: [{ id: 1, band: { name: 'A visible pending band' } }] })
    : accountB.promise; };
  const renderAccount = (id) => createElement(auth.AuthContext.Provider, { value: { user: { id }, getAccessToken: async () => id, refreshSession: async () => id, loading: false, signOut: async () => {} } },
    createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage))));
  try {
    const view = render(renderAccount('account-a'));
    await view.findByText('A visible pending band', { exact: false });
    view.rerender(renderAccount('account-b'));
    assert.equal(view.queryByText('A visible pending band'), null);
    await waitFor(() => assert.equal(calls.length, 2));
    await act(async () => accountB.resolve(response({ bands: [], pending_memberships: [{ id: 2, band: { name: 'B visible pending band' } }] })));
    await view.findByText('B visible pending band', { exact: false });
    assert.equal(view.queryByText('A visible pending band'), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('logout and login to the same user creates a fresh membership request boundary', async () => {
  await load(); const previousFetch = globalThis.fetch; const first = deferred(); const second = deferred(); let reads = 0;
  globalThis.fetch = async (url) => {
    if (String(url).endsWith('/api/v2/me')) return response({ user: { is_platform_admin: false } });
    if (!String(url).endsWith('/api/v2/bands')) return response({});
    reads += 1; return reads === 1 ? first.promise : second.promise;
  };
  const renderAccount = (id) => createElement(auth.AuthContext.Provider, { value: { user: id ? { id } : null, getAccessToken: async () => id, refreshSession: async () => id, loading: false, signOut: async () => {} } },
    createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage))));
  try {
    const view = render(renderAccount('same-user'));
    await waitFor(() => assert.equal(reads, 1));
    view.rerender(renderAccount(null));
    view.rerender(renderAccount('same-user'));
    await waitFor(() => assert.equal(reads, 2));
    await act(async () => first.resolve(response({ bands: [{ id: 1, name: 'Old session band', slug: 'old', membership: { role: 'member' } }], pending_memberships: [] })));
    assert.equal(view.queryByText('Old session band'), null);
    await act(async () => second.resolve(response({ bands: [{ id: 2, name: 'New session band', slug: 'new', membership: { role: 'member' } }], pending_memberships: [] })));
    assert.ok(view.getByRole('button', { name: /New session band/ }));
    assert.equal(view.queryByText('Old session band'), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('admin page verifies server role, loads pending requests, confirms and refreshes decisions', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = []; const previousConfirm = window.confirm; window.confirm = () => true;
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); calls.push({ path, options });
    if (path.endsWith('/api/v2/me')) return response({ user: { is_platform_admin: true } });
    if (path.includes('?status=pending')) return response({ requests: calls.filter((call) => call.options.method === 'PATCH').length ? [] : [{ id: 42, name: 'Quiet Lantern', slug: 'quiet-lantern', description: 'A shared archive', status: 'pending', created_at: '2026-10-12T10:00:00Z' }] });
    return response({ request: { id: 42, status: 'approved' } });
  };
  const authValue = { user: { id: 'admin' }, getAccessToken: async () => 'admin-token', refreshSession: async () => 'admin-token', loading: false };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(MemoryRouter, null, createElement(admin.BandCreationRequestsAdminRoute))));
    await view.findByRole('heading', { name: 'Quiet Lantern' });
    assert.match(calls.find((call) => call.path.endsWith('/api/v2/me')).options.headers.Authorization, /Bearer admin-token/);
    await userEvent.click(view.getByRole('button', { name: '승인' }));
    await view.findByText('대기 중인 밴드 생성 요청이 없습니다.');
    const decision = calls.find((call) => call.options.method === 'PATCH');
    assert.match(decision.path, /\/api\/v2\/platform\/band-creation-requests\/42$/);
    assert.deepEqual(JSON.parse(decision.options.body), { action: 'approve' });
    assert.equal(calls.filter((call) => call.path.includes('?status=pending')).length, 2);
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('superadmin deletion is a manual target-only request with required reason and exact confirmation', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; const calls = []; let confirmed = '';
  window.confirm = (message) => { confirmed = message; return true; };
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); const method = options.method || 'GET'; calls.push({ path, method, body: options.body && JSON.parse(options.body) });
    if (path.endsWith('/api/v2/me')) return response({ user: { is_platform_admin: true } });
    if (path.includes('/platform/band-creation-requests?')) return response({ requests: [] });
    if (path.endsWith('/platform/bands/17/deletions') && method === 'POST') return response({ task_id: 'operator-task', status: 'queued', retry_after_seconds: 0 });
    if (path.endsWith('/platform/deletion-tasks/operator-task')) return response({ task_id: 'operator-task', status: 'completed', resource_type: 'comment', resource_id: 809 });
    throw new Error(`unexpected ${method} ${path}`);
  };
  const authValue = { user: { id: 'verified-operator' }, getAccessToken: async () => 'verified-token', refreshSession: async () => 'verified-token', loading: false };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(MemoryRouter, null, createElement(admin.BandCreationRequestsAdminRoute))));
    await view.findByRole('heading', { name: '대상 지정 콘텐츠 삭제' });
    await userEvent.type(view.getByLabelText('밴드 ID'), '17');
    fireEvent.change(view.getByLabelText('대상 종류'), { target: { value: 'comment' } });
    await userEvent.type(view.getByLabelText('대상 ID'), '809');
    await userEvent.type(view.getByLabelText('삭제 사유'), '사용자 요청에 따른 콘텐츠 삭제 검토');
    await userEvent.type(view.getByLabelText(/확인 문구/), 'DELETE 17/comment/809');
    await userEvent.click(view.getByRole('button', { name: '대상 확인 후 삭제 요청' }));
    await view.findByText('삭제 작업이 완료되었습니다.');
    assert.match(confirmed, /밴드 ID: 17[\s\S]*대상 유형: comment[\s\S]*대상 ID: 809/);
    assert.deepEqual(calls.filter((call) => call.method === 'POST').map((call) => ({ ...call, path: call.path.replace(/^https?:\/\/[^/]+/, '') })), [{ path: '/api/v2/platform/bands/17/deletions', method: 'POST', body: { resource_type: 'comment', resource_id: 809, reason: '사용자 요청에 따른 콘텐츠 삭제 검토', confirm: true } }]);
    assert.equal(calls.some((call) => /\/(media|personal-logs|gallery|songs|rehearsals|members|comments|suggestions)\//.test(call.path)), false, 'the operator path must not fetch a private resource or signed URL');
    assert.equal(calls.some((call) => call.path.endsWith('/platform/deletion-tasks/operator-task')), true);
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('admin page loads requests through the StrictMode effect replay', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => String(url).endsWith('/api/v2/me')
    ? response({ user: { is_platform_admin: true } })
    : response({ requests: [{ id: 43, name: 'Strict Mode Band', slug: 'strict-mode-band', status: 'pending' }] });
  const authValue = { user: { id: 'strict-admin' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
  try {
    const view = render(createElement(StrictMode, null, createElement(auth.AuthContext.Provider, { value: authValue }, createElement(MemoryRouter, null, createElement(admin.BandCreationRequestsAdminRoute)))));
    await view.findByRole('heading', { name: 'Strict Mode Band' });
    assert.ok(view.getByRole('button', { name: '승인' }));
  } finally { globalThis.fetch = previousFetch; }
});

test('direct page denies non-admins and displays 401 and 403 without loading queue data', async () => {
  await load(); const previousFetch = globalThis.fetch;
  for (const [status, isAdmin, expected] of [[200, false, '플랫폼 관리자만 이 페이지를 사용할 수 있습니다.'], [200, 'true', '플랫폼 관리자만 이 페이지를 사용할 수 있습니다.'], [401, false, '로그인 세션을 확인할 수 없습니다. 다시 로그인해 주세요.'], [403, false, '플랫폼 관리자 권한이 없어 요청을 확인할 수 없습니다.']]) {
    const calls = [];
    globalThis.fetch = async (url) => { calls.push(String(url)); return String(url).endsWith('/api/v2/me') ? response({ user: { is_platform_admin: isAdmin } }, status) : response({ requests: [] }); };
    const authValue = { user: { id: `user-${status}` }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(MemoryRouter, null, createElement(admin.BandCreationRequestsAdminRoute))));
    await view.findByText(expected);
    assert.equal(calls.some((url) => url.includes('/platform/band-creation-requests?')), false);
    view.unmount();
  }
  globalThis.fetch = previousFetch;
});

test('admin status and private request results from an old account never appear after account switch', async () => {
  await load(); const previousFetch = globalThis.fetch; const oldMe = deferred(); const oldQueue = deferred();
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith('/api/v2/me')) return options.headers.Authorization === 'Bearer old' ? oldMe.promise : response({ user: { is_platform_admin: false } });
    if (path.includes('?status=pending')) return oldQueue.promise;
    return response({});
  };
  const make = (id) => createElement(auth.AuthContext.Provider, { value: { user: { id }, getAccessToken: async () => id === 'old-user' ? 'old' : 'new', refreshSession: async () => 'new', loading: false } }, createElement(MemoryRouter, null, createElement(admin.BandCreationRequestsAdminRoute)));
  try {
    const view = render(make('old-user'));
    view.rerender(make('new-user'));
    await view.findByText('플랫폼 관리자만 이 페이지를 사용할 수 있습니다.');
    await act(async () => oldMe.resolve(response({ user: { is_platform_admin: true } })));
    assert.equal(view.queryByRole('heading', { name: 'Old private request' }), null);
    assert.equal(view.queryByText('요청을 불러오는 중입니다…'), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('account page no longer exposes retired invite-code submission', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ bands: [], pending_memberships: [] }); };
  const authValue = { user: { id: 'member-join' }, getAccessToken: async () => 'member-token', refreshSession: async () => 'member-token', loading: false, signOut: async () => {} };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandProvider, null, createElement(MemoryRouter, null, createElement(page.BandsPage)))));
    await view.findByText(/초대 링크로 가입/);
    assert.equal(view.queryByLabelText('초대 코드'), null);
    assert.ok(view.getByText(/초대 링크를 열어 밴드 소개/));
    assert.ok(calls.every((path) => !path.includes('invite-codes')));
  } finally { globalThis.fetch = previousFetch; }
});

test('member leaves from the account card, preserves other memberships, and clears only the departed selection', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; const calls = []; const staleRefresh = deferred(); let confirmed = ''; let bandReads = 0;
  window.localStorage.clear(); window.confirm = (message) => { confirmed = message; return true; };
  const first = { id: 51, slug: 'leave-one', name: '탈퇴 밴드', membership: { id: 501, role: 'member', status: 'active' }, permissions: { can_leave: true, can_transfer_ownership: false } };
  const second = { id: 52, slug: 'keep-two', name: '유지 밴드', membership: { id: 502, role: 'admin', status: 'active' }, permissions: { can_leave: true, can_transfer_ownership: false } };
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); calls.push({ path, method: options.method || 'GET' });
    if (path.endsWith('/api/v2/me')) return response({ user: { is_platform_admin: false } });
    if (path.endsWith('/api/v2/bands')) { bandReads += 1; return bandReads === 2 ? staleRefresh.promise : response({ bands: [first, second], pending_memberships: [] }); }
    if (path.endsWith('/api/v2/bands/51/leave') && options.method === 'POST') return response({ membership: { id: 501, band_id: 51, role: 'member', status: 'left' } });
    throw new Error(`unexpected ${options.method || 'GET'} ${path}`);
  };
  const authValue = { user: { id: 'leave-user' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false, signOut: async () => {} };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandProvider, null, createElement(upload.UploadContext.Provider, { value: { tasks: [] } }, createElement(MemoryRouter, null, createElement(page.BandsPage))))));
    await view.findByRole('button', { name: /탈퇴 밴드/ });
    await act(async () => window.dispatchEvent(new window.Event('focus')));
    await waitFor(() => assert.equal(bandReads, 2));
    const button = view.getAllByRole('button', { name: '밴드 탈퇴' })[0];
    await userEvent.click(button);
    await waitFor(() => assert.equal(view.container.querySelector('[data-band-id="51"]'), null));
    await act(async () => staleRefresh.resolve(response({ bands: [first, second], pending_memberships: [] })));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(view.container.querySelector('[data-band-id="51"]'), null, 'a membership read started before leave cannot restore the departed band');
    assert.ok(view.getByRole('button', { name: /유지 밴드/ }));
    assert.match(confirmed, /작성한.*남습니다/);
    assert.match(confirmed, /삭제할 수 없습니다/);
    assert.equal(calls.filter((call) => call.path.endsWith('/api/v2/bands/51/leave') && call.method === 'POST').length, 1);
    assert.equal(calls.some((call) => call.method === 'DELETE'), false);
    assert.equal(window.localStorage.getItem('band-archive.selected-band:leave-user'), null);
    assert.equal(window.location.pathname, '/');
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('owner transfer lists only server-marked active recipients and keeps the new owner if leave must be retried', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; const calls = []; let leaveAttempts = 0;
  window.localStorage.clear(); window.confirm = () => true;
  const initialOwnerBand = { id: 61, slug: 'owner-leave', name: '소유자 밴드', membership: { id: 611, role: 'owner', status: 'active' }, permissions: { can_leave: false, can_transfer_ownership: true } };
  const refreshedAdminBand = { ...initialOwnerBand, membership: { ...initialOwnerBand.membership, role: 'admin' }, permissions: { can_leave: true, can_transfer_ownership: false } }; let detailReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); const method = options.method || 'GET'; calls.push({ path, method, body: options.body && JSON.parse(options.body) });
    if (path.endsWith('/api/v2/me')) return response({ user: { is_platform_admin: false } });
    if (path.endsWith('/api/v2/bands')) return response({ bands: [initialOwnerBand], pending_memberships: [] });
    if (path.endsWith('/api/v2/bands/61/memberships')) return response({ memberships: [
      { id: 611, status: 'active', can_receive_ownership: false, user: { id: 'owner-user', display_name: '현재 소유자' } },
      { id: 612, status: 'active', can_receive_ownership: true, user: { id: 'recipient', display_name: '이전 대상' } },
      { id: 613, status: 'pending', can_receive_ownership: true, user: { id: 'pending', display_name: '대기 멤버' } },
      { id: 614, status: 'active', can_receive_ownership: false, user: { id: 'admin', display_name: '일반 관리자' } },
    ] });
    if (path.endsWith('/api/v2/bands/61/ownership-transfer') && method === 'POST') return response({ membership: { id: 612, role: 'owner', status: 'active' } });
    if (path.endsWith('/api/v2/bands/by-slug/owner-leave')) { detailReads += 1; return detailReads === 1 ? response({}, 500) : response({ band: refreshedAdminBand }); }
    if (path.endsWith('/api/v2/bands/61/leave') && method === 'POST') { leaveAttempts += 1; return leaveAttempts === 1 ? response({}, 409) : response({ membership: { id: 611, band_id: 61, role: 'admin', status: 'left' } }); }
    throw new Error(`unexpected ${method} ${path}`);
  };
  const authValue = { user: { id: 'owner-user' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandProvider, null, createElement(upload.UploadContext.Provider, { value: { tasks: [] } }, createElement(MemoryRouter, null, createElement(page.BandsPage))))));
    await userEvent.click(await view.findByRole('button', { name: '소유권 이전 후 탈퇴' }));
    const target = await view.findByLabelText('소유자 밴드 소유권 이전 대상');
    assert.deepEqual([...target.options].map((option) => option.textContent), ['멤버 선택', '이전 대상']);
    fireEvent.change(target, { target: { value: '612' } });
    await userEvent.click(view.getByRole('button', { name: '이전하고 탈퇴' }));
    await view.findByRole('button', { name: '탈퇴 다시 시도' });
    assert.equal(view.getByRole('heading', { name: '계정과 밴드 관리' }).textContent, '계정과 밴드 관리');
    assert.match(view.getByRole('status').textContent, /소유권 이전은 완료됐지만/);
    assert.equal(calls.filter((call) => call.path.endsWith('/ownership-transfer')).length, 1);
    assert.deepEqual(calls.find((call) => call.path.endsWith('/ownership-transfer')).body, { membership_id: 612 });
    await userEvent.click(view.getByRole('button', { name: '탈퇴 다시 시도' }));
    await view.findByRole('button', { name: '탈퇴 다시 시도' });
    assert.equal(calls.filter((call) => call.path.endsWith('/api/v2/bands/61/leave')).length, 1);
    await userEvent.click(view.getByRole('button', { name: '탈퇴 다시 시도' }));
    await waitFor(() => assert.equal(view.queryByText('소유자 밴드'), null));
    assert.equal(calls.filter((call) => call.path.endsWith('/api/v2/bands/61/leave')).length, 2);
    assert.equal(calls.filter((call) => call.path.endsWith('/api/v2/bands/by-slug/owner-leave')).length, 3);
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('known same-band byte uploads block leave but processing work and another-band uploads do not', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = [];
  window.localStorage.clear();
  const band = { id: 71, slug: 'upload-check', name: '업로드 확인 밴드', membership: { id: 711, role: 'member', status: 'active' }, permissions: { can_leave: true } };
  globalThis.fetch = async (url, options = {}) => { const path = String(url); if (path.endsWith('/api/v2/me')) return response({ user: { is_platform_admin: false } }); if (path.endsWith('/api/v2/bands')) return response({ bands: [band], pending_memberships: [] }); if (options.method === 'POST' && path.endsWith('/api/v2/bands/71/leave')) { writes.push(path); return response({}); } throw new Error(`unexpected ${path}`); };
  const authValue = { user: { id: 'upload-user' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
  const tasks = [
    { id: 'same-byte', bandId: 71, status: 'uploading' },
    { id: 'same-processing', bandId: 71, status: 'processing' },
    { id: 'other-band', bandId: 72, status: 'uploading' },
  ];
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandProvider, null, createElement(upload.UploadContext.Provider, { value: { tasks } }, createElement(MemoryRouter, null, createElement(page.BandsPage))))));
    const leaveButton = await view.findByRole('button', { name: '밴드 탈퇴' });
    assert.equal(leaveButton.disabled, true);
    assert.match(view.getByText(/업로드 1개가 진행 중/).textContent, /현재 탭에서 탈퇴를 막았습니다/);
    assert.ok(view.getByRole('link', { name: '밴드 공간' }));
    assert.equal(writes.length, 0);
  } finally { globalThis.fetch = previousFetch; }
});

test('a late leave response from the prior account cannot remove the next account membership', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; const oldLeave = deferred();
  window.localStorage.clear(); window.confirm = () => true;
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); const token = options.headers?.Authorization;
    if (path.endsWith('/api/v2/me')) return response({ user: { is_platform_admin: false } });
    if (path.endsWith('/api/v2/bands')) return response({ bands: [{ id: 81, slug: 'shared-membership', name: token === 'Bearer account-b' ? 'B 밴드' : 'A 밴드', membership: { id: token === 'Bearer account-b' ? 812 : 811, role: 'member', status: 'active' }, permissions: { can_leave: true } }], pending_memberships: [] });
    if (path.endsWith('/api/v2/bands/81/leave')) return token === 'Bearer account-a' ? oldLeave.promise : response({ membership: { id: 812, band_id: 81, status: 'left' } });
    throw new Error(`unexpected ${path}`);
  };
  const make = (id) => createElement(auth.AuthContext.Provider, { value: { user: { id }, getAccessToken: async () => id, refreshSession: async () => id, loading: false } }, createElement(bands.BandProvider, null, createElement(upload.UploadContext.Provider, { value: { tasks: [] } }, createElement(MemoryRouter, null, createElement(page.BandsPage)))));
  try {
    const view = render(make('account-a'));
    await userEvent.click(await view.findByRole('button', { name: '밴드 탈퇴' }));
    view.rerender(make('account-b'));
    await view.findByRole('button', { name: /B 밴드/ });
    await act(async () => oldLeave.resolve(response({ membership: { id: 811, band_id: 81, status: 'left' } })));
    assert.ok(view.getByRole('button', { name: /B 밴드/ }));
    assert.equal(view.queryByText('A 밴드'), null);
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('same-account storage leave signal removes only its band and retains the other account selection', async () => {
  await load(); const previousFetch = globalThis.fetch;
  window.localStorage.clear();
  const staleRead = deferred(); let bandReads = 0;
  const firstBand = { id: 91, slug: 'remote-left', name: '다른 탭에서 탈퇴', membership: { id: 911, role: 'member' }, permissions: { can_leave: true } };
  const secondBand = { id: 92, slug: 'still-here', name: '계속 가입 중', membership: { id: 912, role: 'member' }, permissions: { can_leave: true } };
  globalThis.fetch = async (url) => String(url).endsWith('/api/v2/me')
    ? response({ user: { is_platform_admin: false } })
    : ((bandReads += 1) === 1 ? response({ bands: [firstBand, secondBand], pending_memberships: [] })
      : bandReads === 2 ? staleRead.promise : response({ bands: [secondBand], pending_memberships: [] }));
  const authValue = { user: { id: 'cross-tab-user' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandProvider, null, createElement(upload.UploadContext.Provider, { value: { tasks: [] } }, createElement(MemoryRouter, null, createElement(page.BandsPage))))));
    await view.findByRole('button', { name: /다른 탭에서 탈퇴/ });
    const selectedBefore = window.localStorage.getItem('band-archive.selected-band:cross-tab-user');
    assert.equal(selectedBefore, '91');
    await act(async () => window.dispatchEvent(new window.Event('focus')));
    await waitFor(() => assert.equal(bandReads, 2));
    await act(async () => window.dispatchEvent(new window.StorageEvent('storage', { key: 'band-archive.membership-change:cross-tab-user', newValue: JSON.stringify({ bandId: 91, bandSlug: 'remote-left', nonce: 1 }), storageArea: window.localStorage })));
    await waitFor(() => assert.equal(bandReads, 3));
    await act(async () => staleRead.resolve(response({ bands: [firstBand, secondBand], pending_memberships: [] })));
    await waitFor(() => assert.equal(view.container.querySelector('[data-band-id="91"]'), null));
    assert.ok(view.getByRole('button', { name: /계속 가입 중/ }));
    assert.equal(window.localStorage.getItem('band-archive.selected-band:cross-tab-user'), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('a solo owner with no server-eligible transfer target is told that leaving is blocked', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); if (path.endsWith('/api/v2/me')) return response({ user: { is_platform_admin: false } });
    if (path.endsWith('/api/v2/bands')) return response({ bands: [{ id: 101, slug: 'solo-owner', name: '혼자 남은 밴드', membership: { id: 1011, role: 'owner', status: 'active' }, permissions: { can_leave: false, can_transfer_ownership: true } }], pending_memberships: [] });
    if (path.endsWith('/api/v2/bands/101/memberships')) return response({ memberships: [{ id: 1011, status: 'active', can_receive_ownership: false, user: { id: 'solo-user', display_name: '혼자 남은 owner' } }] });
    if (options.method === 'POST') writes.push(path);
    return response({});
  };
  const authValue = { user: { id: 'solo-user' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', loading: false };
  try {
    const view = render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandProvider, null, createElement(upload.UploadContext.Provider, { value: { tasks: [] } }, createElement(MemoryRouter, null, createElement(page.BandsPage))))));
    await userEvent.click(await view.findByRole('button', { name: '소유권 이전 후 탈퇴' }));
    await view.findByText(/소유권을 이전할 수 있는 다른 활성 멤버가 없습니다/);
    assert.equal(writes.length, 0);
    assert.ok(view.getByRole('button', { name: '이전하고 탈퇴' }).disabled);
  } finally { globalThis.fetch = previousFetch; }
});
