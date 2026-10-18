import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement, useEffect } from 'react';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, FormData: dom.window.FormData });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; let reject; const promise = new Promise((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; };
const user = userEvent.setup({ document: dom.window.document });
let vite; let onboarding; let auth; let bands;
const load = async () => { if (onboarding) return; vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); onboarding = await vite.ssrLoadModule('/src/components/bands/BandOnboarding.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); bands = await vite.ssrLoadModule('/src/context/BandContext.jsx'); };
after(async () => { await vite?.close(); dom.window.close(); }); afterEach(() => cleanup());
const account = (id = 'u1') => ({ user: id ? { id } : null, getAccessToken: async () => id, refreshSession: async () => id, loading: false });
const Login = () => createElement('p', null, `login:${useLocation().search}`);
const Go = ({ target }) => { const navigate = useNavigate(); useEffect(() => { navigate(target); }, [navigate, target]); return null; };
const App = ({ entry, authValue = account(), manage = false, discover = false, create = false, bandContextValue = { reload: () => {} } }) => {
  const routes = createElement(Routes, null,
    create ? createElement(Route, { path: '/bands/request', element: createElement(onboarding.BandCreationRequestPage) }) : null,
    manage ? createElement(Route, { path: '/bands/:bandSlug/manage', element: createElement(onboarding.BandManageRoute) }) : null,
    createElement(Route, { path: '/login', element: createElement(Login) }));
  return createElement(auth.AuthContext.Provider, { value: authValue }, discover
    ? createElement(bands.BandProvider, null, createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null,
      createElement(Route, { path: '/bands/discover', element: createElement(onboarding.DiscoverBandsPage) }),
      createElement(Route, { path: '/login', element: createElement(Login) }))))
    : createElement(bands.BandContext.Provider, { value: bandContextValue }, createElement(MemoryRouter, { initialEntries: [entry] }, manage && createElement(Go, { target: entry }), routes)));
};

test('visitor discover preserves a safe login return and join becomes pending once', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, request = {}) => { calls.push([String(url), request]); return response({ bands: [{ id: 7, slug: 'public', name: 'Public' }], pagination: { total: 1, has_more: false } }); };
  try { const visitor = render(createElement(App, { entry: '/bands/discover', authValue: account(null), discover: true })); await visitor.findByText('Public'); await userEvent.click(visitor.getByRole('button', { name: '로그인 후 가입 요청' })); await visitor.findByText('login:?returnTo=%2Fbands%2Fdiscover'); assert.ok(calls.every(([url]) => !url.includes('invite'))); visitor.unmount();
    let joined = 0; let membershipReads = 0; globalThis.fetch = async (url) => { if (String(url).endsWith('/api/v2/bands')) { membershipReads += 1; return response({ bands: [], pending_memberships: membershipReads > 1 ? [{ id: 1, band: { id: 7 } }] : [] }); } if (String(url).includes('join-requests')) { joined += 1; return response({ membership: { id: 1 }, created: true }, 201); } return response({ bands: [{ id: 7, slug: 'public', name: 'Public' }], pagination: { total: 1, has_more: false } }); };
    const member = render(createElement(App, { entry: '/bands/discover', discover: true })); await member.findByText('Public'); const join = member.getByRole('button', { name: '가입 요청' }); await userEvent.click(join); assert.equal(joined, 0); await userEvent.click(member.getByRole('button', { name: '가입 요청 보내기' })); await waitFor(() => assert.equal(joined, 1)); await member.findByRole('button', { name: '승인 대기 중' }); await userEvent.click(member.getByRole('button', { name: '승인 대기 중' })); assert.equal(joined, 1);
  } finally { globalThis.fetch = previous; }
});

test('discover bands uses the shared accessible return link styling', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async () => response({ bands: [], pagination: { total: 0, has_more: false } });
  try {
    const view = render(createElement(App, { entry: '/bands/discover', discover: true }));
    const back = await view.findByRole('link', { name: '내 밴드' });
    assert.equal(back.getAttribute('href'), '/bands');
    assert.ok(back.classList.contains('bands-return-link'));
  } finally { globalThis.fetch = previous; }
});

test('invite code stays in a POST body, clears the input, and has a generic invalid result', async () => {
  await load(); const previous = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => { writes.push([String(url), options]); return response({}, 404); };
  try { const view = render(createElement(auth.AuthContext.Provider, { value: account() }, createElement(onboarding.InviteCodeEntry))); const input = view.getByLabelText('초대 코드'); await userEvent.type(input, 'secret-capability'); await userEvent.click(view.getByRole('button', { name: '가입' })); await view.findByText('초대 코드를 사용할 수 없습니다.'); assert.equal(input.value, ''); assert.equal(writes.length, 1); assert.ok(writes[0][0].endsWith('/api/v2/invite-codes/join')); assert.equal(writes[0][0].includes('secret-capability'), false); assert.deepEqual(JSON.parse(writes[0][1].body), { code: 'secret-capability' }); } finally { globalThis.fetch = previous; }
});

test('join introduction is optional plain text, bounded to 300 code points, trimmed and single-flight', async () => {
  await load(); const previous = globalThis.fetch; const pending = deferred(); const writes = [];
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/api/v2/bands')) return response({ bands: [], pending_memberships: [] });
    if (String(url).includes('join-requests')) { writes.push(JSON.parse(options.body)); return pending.promise; }
    return response({ bands: [{ id: 7, slug: 'public', name: 'Public' }], pagination: { total: 1, has_more: false } });
  };
  try {
    const view = render(createElement(App, { entry: '/bands/discover', discover: true }));
    await view.findByRole('button', { name: '가입 요청' }); await userEvent.click(view.getByRole('button', { name: '가입 요청' }));
    const input = view.getByLabelText('간단한 자기소개 (선택)');
    fireEvent.change(input, { target: { value: 'a'.repeat(301) } });
    // Use the native input event path for controlled textareas in jsdom.
    await userEvent.type(input, ' 기타와 보컬을 해요. ');
    const send = view.getByRole('button', { name: '가입 요청 보내기' });
    assert.equal(send.disabled, true); await user.clear(input); await userEvent.type(input, ' 기타와 보컬을 해요. ');
    await userEvent.click(send); fireEvent.submit(input.closest('form')); assert.equal(writes.length, 1);
    assert.deepEqual(writes[0], { introduction: '기타와 보컬을 해요.' });
    await act(async () => pending.resolve(response({ membership: { id: 1 }, created: true }, 201)));
    await waitFor(() => assert.equal(view.queryByLabelText('간단한 자기소개 (선택)'), null));
  } finally { globalThis.fetch = previous; }
});

test('join account switch clears draft and ignores the previous account response', async () => {
  await load(); const previous = globalThis.fetch; const pending = deferred(); let writes = 0;
  globalThis.fetch = async (url) => {
    if (String(url).endsWith('/api/v2/bands')) return response({ bands: [], pending_memberships: [] });
    if (String(url).includes('join-requests')) { writes += 1; return pending.promise; }
    return response({ bands: [{ id: 7, slug: 'public', name: 'Public' }], pagination: { total: 1, has_more: false } });
  };
  try {
    const view = render(createElement(App, { entry: '/bands/discover', discover: true, authValue: account('a') }));
    await view.findByRole('button', { name: '가입 요청' }); await userEvent.click(view.getByRole('button', { name: '가입 요청' }));
    await userEvent.type(view.getByLabelText('간단한 자기소개 (선택)'), 'private draft'); await userEvent.click(view.getByRole('button', { name: '가입 요청 보내기' }));
    view.rerender(createElement(App, { entry: '/bands/discover', discover: true, authValue: account('b') }));
    assert.equal(view.queryByDisplayValue('private draft'), null);
    await act(async () => pending.resolve(response({}, 400)));
    await view.findByRole('button', { name: '가입 요청' }); assert.equal(view.queryByRole('alert'), null); assert.equal(writes, 1);
  } finally { globalThis.fetch = previous; }
});

test('manager sees pending introduction as text and KST timestamp, never active introduction', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const path = String(url);
    if (path.includes('/by-slug/a')) return response({ band: { id: 1, slug: 'a', name: 'A', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (path.endsWith('/memberships')) return response({ memberships: [{ id: 1, status: 'pending', user: { display_name: 'Applicant' }, requested_at: '2026-10-17T00:30:00Z', introduction: '<img src=x onerror=alert(1)>\n기타 연주' }, { id: 2, status: 'active', role: 'member', user: { display_name: 'Member' }, introduction: 'must-not-display' }] });
    if (path.endsWith('/announcement')) return response({ announcement: null });
    return response({ invite_codes: [] });
  };
  try {
    const view = render(createElement(App, { entry: '/bands/a/manage', manage: true })); await view.findByText('Applicant');
    assert.ok(view.getByText('2026-10-17 09:30 KST'));
    const text = view.container.querySelector('.membership-introduction'); assert.equal(text.textContent, '<img src=x onerror=alert(1)>\n기타 연주');
    assert.equal(text.querySelector('img'), null); assert.equal(view.queryByText('must-not-display'), null);
  } finally { globalThis.fetch = previous; }
});

test('creation form has one in-flight submit and reports its status', async () => {
  await load(); const previous = globalThis.fetch; const first = deferred(); let writes = 0;
  globalThis.fetch = async (url) => { if (String(url).includes('band-creation-requests')) { writes += 1; return first.promise; } throw new Error('unexpected'); };
  try { const view = render(createElement(App, { entry: '/bands/request', create: true })); await userEvent.type(view.getByLabelText('밴드 이름'), '새 밴드'); await userEvent.type(view.getByLabelText('주소(slug)'), 'new-band'); const button = view.getByRole('button', { name: '생성 요청 보내기' }); await userEvent.click(button); await userEvent.click(button); assert.equal(writes, 1); await act(async () => first.resolve(response({ request: { name: '새 밴드', status: 'pending' } }, 201))); await view.findByText(/승인 대기 중/); } finally { globalThis.fetch = previous; }
});

test('management hides non-manager and ignores a late A membership decision after route switch', async () => {
  await load(); const previous = globalThis.fetch; const decision = deferred(); const aDetail = { band: { id: 1, slug: 'a', name: 'A', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } }; const bDetail = { band: { id: 2, slug: 'b', name: 'B', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } };
  globalThis.fetch = async (url, options = {}) => { const value = String(url); if (value.includes('/by-slug/a')) return response(aDetail); if (value.includes('/by-slug/b')) return response(bDetail); if (value.endsWith('/announcement')) return response({ announcement: null }); if (value.includes('/bands/1/memberships')) return response({ memberships: [{ id: 12, status: 'pending', user: { display_name: 'A 요청' } }] }); if (value.includes('/bands/2/memberships')) return response({ memberships: [] }); if (value.includes('/invite-codes') && options.method !== 'POST') return response({ invite_codes: [] }); if (value.includes('/memberships/12')) return decision.promise; throw new Error(`unexpected ${value}`); };
  try { const view = render(createElement(App, { entry: '/bands/a/manage', manage: true })); await view.findByText('A 요청'); await userEvent.click(view.getByRole('button', { name: '승인' })); view.rerender(createElement(App, { entry: '/bands/b/manage', manage: true })); await view.findByText('B 관리'); await act(async () => decision.resolve(response({ membership: { id: 12 } }))); await new Promise((done) => setTimeout(done, 0)); assert.equal(view.queryByText('A 요청'), null); assert.equal(view.getByText('B 관리').textContent, 'B 관리'); view.unmount();
    globalThis.fetch = async (url) => String(url).includes('/by-slug/member') ? response({ band: { id: 4, slug: 'member', permissions: { can_manage_members: false, can_manage_invites: false } } }) : response({}); const platform = { ...account('platform'), user: { id: 'platform', app_metadata: { role: 'superadmin' } } }; const denied = render(createElement(App, { entry: '/bands/member/manage', manage: true, authValue: platform })); await denied.findByText('찾을 수 없는 관리 공간입니다.'); assert.equal(denied.queryByText('초대 코드'), null); assert.equal(denied.queryByText('대표 사진'), null); const myBandsLink = denied.getByRole('link', { name: '내 밴드로 이동' }); assert.equal(myBandsLink.getAttribute('href'), '/bands'); assert.ok(myBandsLink.classList.contains('bands-return-link'));
  } finally { globalThis.fetch = previous; }
});

test('management clears A privileged UI synchronously when the AuthContext identity changes in place', async () => {
  await load(); const previous = globalThis.fetch; const bDetail = deferred();
  const a = { band: { id: 1, slug: 'club', name: 'A', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true } } };
  const b = { band: { id: 2, slug: 'club', name: 'B', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } };
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url); const token = options.headers?.Authorization;
    if (value.includes('/by-slug/club')) return token === 'Bearer b' ? bDetail.promise : response(a);
    if (value.endsWith('/announcement')) return response({ announcement: null });
    if (value.includes('/bands/1/memberships')) return response({ memberships: [{ id: 11, status: 'pending', user: { display_name: 'A 요청' } }] });
    if (value.includes('/bands/2/memberships')) return response({ memberships: [] });
    if (value.includes('/bands/1/invite-codes') && options.method === 'POST') return response({ code: 'a-raw-invite' }, 201);
    if (value.includes('/bands/1/invite-codes')) return response({ invite_codes: [] });
    if (value.includes('/bands/2/invite-codes')) return response({ invite_codes: [] });
    if (value.includes('/memberships/11')) return response({}, 500);
    throw new Error(`unexpected ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/club/manage', manage: true, authValue: account('a') }));
    await view.findByText('A 요청');
    await userEvent.click(view.getByRole('button', { name: '새 초대 코드 만들기' }));
    await view.findByText('a-raw-invite');
    await userEvent.click(view.getByRole('button', { name: '승인' }));
    await view.findByRole('alert');

    // The same MemoryRouter remains mounted; changing only the provider value
    // must not leave A's privileged state painted while B is still loading.
    view.rerender(createElement(App, { entry: '/bands/club/manage', manage: true, authValue: account('b') }));
    assert.equal(view.queryByText('a-raw-invite'), null);
    assert.equal(view.queryByText('A 요청'), null);
    assert.equal(view.queryByRole('alert'), null);
    assert.equal(view.queryByRole('button', { name: '승인' }), null);
    assert.equal(view.queryByRole('button', { name: '새 초대 코드 만들기' }), null);

    await act(async () => bDetail.resolve(response(b)));
    await view.findByText('B 관리');
  } finally { globalThis.fetch = previous; }
});

test('management ignores late A reads after an in-place AuthContext switch to B on the same slug', async () => {
  await load(); const previous = globalThis.fetch; const aMembers = deferred(); const aInvites = deferred(); let aDetailReads = 0;
  const a = { band: { id: 1, slug: 'club', name: 'A', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } };
  const b = { band: { id: 2, slug: 'club', name: 'B', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } };
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url); const token = options.headers?.Authorization;
    if (value.includes('/by-slug/club')) { if (token === 'Bearer a') aDetailReads += 1; return response(token === 'Bearer b' ? b : a); }
    if (value.endsWith('/announcement')) return response({ announcement: null });
    if (value.includes('/bands/1/memberships')) return aMembers.promise;
    if (value.includes('/bands/1/invite-codes')) return aInvites.promise;
    if (value.includes('/bands/2/memberships')) return response({ memberships: [{ id: 22, status: 'pending', user: { display_name: 'B 요청' } }] });
    if (value.includes('/bands/2/invite-codes')) return response({ invite_codes: [] });
    throw new Error(`unexpected ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/club/manage', manage: true, authValue: account('a') }));
    await waitFor(() => assert.equal(aDetailReads, 1));
    view.rerender(createElement(App, { entry: '/bands/club/manage', manage: true, authValue: account('b') }));
    await view.findByText('B 요청');
    await act(async () => { aMembers.resolve(response({ memberships: [{ id: 12, status: 'pending', user: { display_name: 'A 늦은 요청' } }] })); aInvites.resolve(response({}, 500)); });
    await new Promise((done) => setTimeout(done, 0));
    assert.equal(view.queryByText('A 늦은 요청'), null);
    assert.equal(view.queryByText('요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.'), null);
    assert.equal(view.queryByText('B 요청')?.textContent, 'B 요청');
    assert.equal(aDetailReads, 1, 'late A responses must not trigger another A load');
  } finally { globalThis.fetch = previous; }
});

test('management keeps the newest mutation refetch when superseded reads resolve in reverse order', async () => {
  await load(); const previous = globalThis.fetch; const create = deferred(); const approve = deferred();
  const oldMembers = deferred(); const oldInvites = deferred(); const newMembers = deferred(); const newInvites = deferred(); const followupSignals = [];
  const detail = { band: { id: 1, slug: 'club', name: 'Club', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } };
  let membershipReads = 0; let inviteReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url);
    if (value.includes('/by-slug/club')) return response(detail);
    if (value.endsWith('/announcement')) return response({ announcement: null });
    if (value.includes('/bands/1/memberships')) {
      membershipReads += 1;
      if (membershipReads === 1) return response({ memberships: [{ id: 12, status: 'pending', user: { display_name: '기존 요청' } }] });
      followupSignals.push(options.signal); return membershipReads === 2 ? oldMembers.promise : newMembers.promise;
    }
    if (value.includes('/bands/1/invite-codes') && options.method === 'POST') return create.promise;
    if (value.includes('/bands/1/invite-codes')) {
      inviteReads += 1;
      if (inviteReads === 1) return response({ invite_codes: [] });
      followupSignals.push(options.signal); return inviteReads === 2 ? oldInvites.promise : newInvites.promise;
    }
    if (value.includes('/memberships/12')) return approve.promise;
    throw new Error(`unexpected ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/club/manage', manage: true }));
    await view.findByText('기존 요청');
    await userEvent.click(view.getByRole('button', { name: '새 초대 코드 만들기' }));
    await userEvent.click(view.getByRole('button', { name: '승인' }));

    await act(async () => create.resolve(response({ code: 'old-raw-code' }, 201)));
    await waitFor(() => assert.equal(membershipReads, 2));
    await act(async () => approve.resolve(response({ membership: { id: 12 } })));
    await waitFor(() => assert.equal(membershipReads, 3));
    assert.ok(followupSignals.slice(0, 2).every((signal) => signal.aborted), 'the earlier refetch is aborted before its ignored responses resolve');

    await act(async () => { newMembers.resolve(response({ memberships: [{ id: 14, status: 'pending', user: { display_name: 'New' } }] })); newInvites.resolve(response({ invite_codes: [] })); });
    await view.findByText('New');
    await act(async () => { oldMembers.resolve(response({ memberships: [{ id: 13, status: 'pending', user: { display_name: 'Old' } }] })); oldInvites.resolve(response({ invite_codes: [] })); });
    await new Promise((done) => setTimeout(done, 0));
    assert.equal(view.queryByText('Old'), null);
    assert.equal(view.queryByText('New')?.textContent, 'New');
  } finally { globalThis.fetch = previous; }
});

test('management return control is a keyboard-focusable canonical RouterLink', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async (url) => { const value = String(url);
    if (value.includes('/by-slug/requested')) return response({ band: { id: 88, slug: 'canonical-band', name: '이름이 긴 밴드', permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (value.endsWith('/bands/88/memberships')) return response({ memberships: [] }); if (value.endsWith('/bands/88/invite-codes')) return response({ invite_codes: [] }); if (value.endsWith('/bands/88/announcement')) return response({ announcement: null });
    throw new Error(`unexpected ${value}`);
  };
  try { const view = render(createElement(App, { entry: '/bands/requested/manage', manage: true })); await view.findByText('이름이 긴 밴드 관리'); const link = view.getByRole('link', { name: '밴드 공간으로 돌아가기' }); assert.equal(link.getAttribute('href'), '/bands/canonical-band'); assert.equal(link.tabIndex, 0); assert.ok(link.classList.contains('bands-return-link')); await user.tab(); assert.equal(document.activeElement, link); }
  finally { globalThis.fetch = previous; }
});

test('owner management page links to the isolated logo management route only with server permission', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const path = String(url);
    if (path.includes('/by-slug/logo-owner')) return response({ band: { id: 81, slug: 'logo-owner', name: '로고 밴드', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true, can_manage_logo: true } } });
    if (path.endsWith('/memberships')) return response({ memberships: [] });
    if (path.endsWith('/invite-codes')) return response({ invite_codes: [] });
    if (path.endsWith('/announcement')) return response({ announcement: null });
    throw new Error(`unexpected ${path}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/logo-owner/manage', manage: true }));
    const link = await view.findByRole('link', { name: '로고 관리' });
    assert.equal(link.getAttribute('href'), '/bands/logo-owner/manage/logo');
  } finally { globalThis.fetch = previous; }
});

test('owner profile editor requires explicit permission and saves only the trimmed-contract field twice', async () => {
  await load(); const previous = globalThis.fetch; const writes = []; let reloads = 0;
  let profileDescription = '처음 소개';
  const owner = { id: 51, slug: 'profile', name: '프로필 밴드', description: profileDescription, membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true, can_edit_band_profile: true } };
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url); const method = options.method || 'GET';
    if (value.endsWith('/by-slug/profile')) return response({ band: owner });
    if (value.endsWith('/bands/51/memberships')) return response({ memberships: [] });
    if (value.endsWith('/bands/51/invite-codes')) return response({ invite_codes: [] });
    if (value.endsWith('/bands/51/announcement')) return response({ announcement: null });
    if (value.endsWith('/bands/51/profile') && method === 'PATCH') { const body = JSON.parse(options.body); writes.push(body); profileDescription = body.description.trim() || null; owner.description = profileDescription; return response({ band: { ...owner, description: profileDescription } }); }
    throw new Error(`unexpected ${method} ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/profile/manage', manage: true, bandContextValue: { reload: () => { reloads += 1; } } }));
    await view.findByText('처음 소개'); await user.click(view.getByRole('button', { name: '소개 수정' }));
    const field = view.getByLabelText('소개'); assert.equal(field.maxLength, 2000);
    await user.clear(field); await user.type(field, '  첫 저장  '); await user.click(view.getByRole('button', { name: '소개 저장' }));
    await view.findByText('첫 저장'); await user.click(view.getByRole('button', { name: '소개 수정' }));
    await user.clear(view.getByLabelText('소개')); await user.type(view.getByLabelText('소개'), '두 번째'); await user.click(view.getByRole('button', { name: '소개 저장' }));
    await view.findByText('두 번째'); assert.deepEqual(writes, [{ description: '  첫 저장  ' }, { description: '두 번째' }]); assert.equal(reloads, 2);
    owner.permissions.can_edit_band_profile = false;
    view.unmount();
    const admin = { ...owner, membership: { role: 'admin' } };
    globalThis.fetch = async (url) => String(url).endsWith('/by-slug/profile') ? response({ band: admin }) : String(url).endsWith('/memberships') ? response({ memberships: [] }) : String(url).endsWith('/invite-codes') ? response({ invite_codes: [] }) : response({ announcement: null });
    const denied = render(createElement(App, { entry: '/bands/profile/manage', manage: true })); await denied.findByText('프로필 밴드 관리');
    assert.equal(denied.queryByRole('button', { name: '소개 수정' }), null); assert.equal(denied.queryByRole('textbox', { name: '소개' }), null);
  } finally { globalThis.fetch = previous; }
});

test('profile 403 closes edit and preserves admin management after detail-only permission recheck', async () => {
  await load(); const previous = globalThis.fetch; let profileWrite = 0; let detailReads = 0;
  const initial = { band: { id: 61, slug: 'roleloss', name: '역할 변경', description: '기존', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true, can_edit_band_profile: true } } };
  const afterLoss = { band: { ...initial.band, membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true, can_edit_band_profile: false } } };
  globalThis.fetch = async (url, options = {}) => { const value = String(url); const method = options.method || 'GET';
    if (value.endsWith('/by-slug/roleloss')) { detailReads += 1; return response(detailReads > 1 ? afterLoss : initial); }
    if (value.endsWith('/bands/61/memberships')) return response({ memberships: [] }); if (value.endsWith('/bands/61/invite-codes')) return response({ invite_codes: [] }); if (value.endsWith('/bands/61/announcement')) return response({ announcement: null });
    if (value.endsWith('/bands/61/profile') && method === 'PATCH') { profileWrite += 1; return response({}, 403); }
    throw new Error(`unexpected ${method} ${value}`);
  };
  try { const view = render(createElement(App, { entry: '/bands/roleloss/manage', manage: true })); await view.findByText('역할 변경 관리'); await user.click(view.getByRole('button', { name: '소개 수정' })); await user.type(view.getByLabelText('소개'), 'draft'); await user.click(view.getByRole('button', { name: '소개 저장' })); await view.findByText('소개를 수정할 권한이 변경되었습니다.'); assert.equal(profileWrite, 1); assert.equal(detailReads, 2); assert.ok(view.getByText('역할 변경 관리')); assert.equal(view.queryByRole('button', { name: '소개 수정' }), null); assert.ok(view.getByRole('heading', { name: '가입 요청' })); }
  finally { globalThis.fetch = previous; }
});

test('profile 404 rechecks membership and clears the management page when the band is no longer accessible', async () => {
  await load(); const previous = globalThis.fetch; let detailReads = 0;
  const band = { band: { id: 62, slug: 'deleted', name: '삭제된 밴드', description: '기존', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true, can_edit_band_profile: true } } };
  globalThis.fetch = async (url, options = {}) => { const value = String(url); const method = options.method || 'GET';
    if (value.endsWith('/by-slug/deleted')) { detailReads += 1; return detailReads === 1 ? response(band) : response({}, 404); }
    if (value.endsWith('/bands/62/memberships')) return response({ memberships: [] }); if (value.endsWith('/bands/62/invite-codes')) return response({ invite_codes: [] }); if (value.endsWith('/bands/62/announcement')) return response({ announcement: null });
    if (value.endsWith('/bands/62/profile') && method === 'PATCH') return response({}, 404);
    throw new Error(`unexpected ${method} ${value}`);
  };
  try { const view = render(createElement(App, { entry: '/bands/deleted/manage', manage: true })); await view.findByText('삭제된 밴드 관리'); await user.click(view.getByRole('button', { name: '소개 수정' })); await user.type(view.getByLabelText('소개'), 'new'); await user.click(view.getByRole('button', { name: '소개 저장' })); await view.findByText('찾을 수 없는 관리 공간입니다.'); assert.equal(view.queryByText('삭제된 밴드 관리'), null); assert.equal(view.queryByRole('button', { name: '새 초대 코드 만들기' }), null); assert.equal(detailReads, 2); }
  finally { globalThis.fetch = previous; }
});

test('profile save failure keeps the draft available for retry', async () => {
  await load(); const previous = globalThis.fetch; let writes = 0;
  const band = { band: { id: 63, slug: 'retry', name: '재시도 밴드', description: '', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true, can_edit_band_profile: true } } };
  globalThis.fetch = async (url, options = {}) => { const value = String(url); const method = options.method || 'GET';
    if (value.endsWith('/by-slug/retry')) return response(band); if (value.endsWith('/bands/63/memberships')) return response({ memberships: [] }); if (value.endsWith('/bands/63/invite-codes')) return response({ invite_codes: [] }); if (value.endsWith('/bands/63/announcement')) return response({ announcement: null });
    if (value.endsWith('/bands/63/profile') && method === 'PATCH') { writes += 1; return response({}, 500); }
    throw new Error(`unexpected ${method} ${value}`);
  };
  try { const view = render(createElement(App, { entry: '/bands/retry/manage', manage: true })); await view.findByText('재시도 밴드 관리'); await user.click(view.getByRole('button', { name: '소개 수정' })); const field = view.getByLabelText('소개'); await user.type(field, '보존할 초안'); await user.click(view.getByRole('button', { name: '소개 저장' })); await view.findByRole('alert'); assert.equal(view.getByLabelText('소개').value, '보존할 초안'); assert.equal(writes, 1); }
  finally { globalThis.fetch = previous; }
});

test('late profile save from account A cannot repaint account B after identity switch', async () => {
  await load(); const previous = globalThis.fetch; const save = deferred(); let writes = 0;
  const makeBand = (id, name) => ({ band: { id, slug: 'same', name, description: `${name} 소개`, membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true, can_edit_band_profile: true } } });
  globalThis.fetch = async (url, options = {}) => { const value = String(url); const token = options.headers?.Authorization;
    if (value.endsWith('/by-slug/same')) return response(token === 'Bearer b' ? makeBand(72, 'B') : makeBand(71, 'A'));
    if (value.endsWith('/bands/71/memberships') || value.endsWith('/bands/72/memberships')) return response({ memberships: [] });
    if (value.endsWith('/bands/71/invite-codes') || value.endsWith('/bands/72/invite-codes')) return response({ invite_codes: [] });
    if (value.endsWith('/bands/71/announcement') || value.endsWith('/bands/72/announcement')) return response({ announcement: null });
    if (value.endsWith('/bands/71/profile') && options.method === 'PATCH') { writes += 1; return save.promise; }
    throw new Error(`unexpected ${value}`);
  };
  try { const view = render(createElement(App, { entry: '/bands/same/manage', manage: true, authValue: account('a') })); await view.findByText('A 관리'); await user.click(view.getByRole('button', { name: '소개 수정' })); await user.clear(view.getByLabelText('소개')); await user.type(view.getByLabelText('소개'), 'A late draft'); await user.click(view.getByRole('button', { name: '소개 저장' })); await waitFor(() => assert.equal(writes, 1));
    view.rerender(createElement(App, { entry: '/bands/same/manage', manage: true, authValue: account('b') })); await view.findByText('B 관리'); await act(async () => save.resolve(response({ band: { ...makeBand(71, 'A').band, description: 'stale A' } }))); await new Promise((done) => setTimeout(done, 0));
    assert.equal(view.queryByText('stale A'), null); assert.ok(view.getByText('B 소개')); assert.equal(view.queryByRole('button', { name: '소개 저장' }), null);
  } finally { globalThis.fetch = previous; }
});

test('late profile save failure from account A cannot paint an error into account B', async () => {
  await load(); const previous = globalThis.fetch; const save = deferred();
  const makeBand = (id, name) => ({ band: { id, slug: 'same-failure', name, description: `${name} 소개`, membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true, can_edit_band_profile: true } } });
  globalThis.fetch = async (url, options = {}) => { const value = String(url); const token = options.headers?.Authorization;
    if (value.endsWith('/by-slug/same-failure')) return response(token === 'Bearer b' ? makeBand(82, 'B') : makeBand(81, 'A'));
    if (value.endsWith('/bands/81/memberships') || value.endsWith('/bands/82/memberships')) return response({ memberships: [] });
    if (value.endsWith('/bands/81/invite-codes') || value.endsWith('/bands/82/invite-codes')) return response({ invite_codes: [] });
    if (value.endsWith('/bands/81/announcement') || value.endsWith('/bands/82/announcement')) return response({ announcement: null });
    if (value.endsWith('/bands/81/profile') && options.method === 'PATCH') return save.promise;
    throw new Error(`unexpected ${value}`);
  };
  try { const view = render(createElement(App, { entry: '/bands/same-failure/manage', manage: true, authValue: account('a') })); await view.findByText('A 관리'); await user.click(view.getByRole('button', { name: '소개 수정' })); await user.type(view.getByLabelText('소개'), 'late failure'); await user.click(view.getByRole('button', { name: '소개 저장' }));
    view.rerender(createElement(App, { entry: '/bands/same-failure/manage', manage: true, authValue: account('b') })); await view.findByText('B 관리'); await act(async () => save.reject(new Error('stale failure'))); await new Promise((done) => setTimeout(done, 0));
    assert.equal(view.queryByText('소개를 저장하지 못했습니다. 입력 내용을 확인하고 다시 시도해 주세요.'), null); assert.ok(view.getByText('B 소개'));
  } finally { globalThis.fetch = previous; }
});

test('manager edits announcement visibility repeatedly and pages the featured photo picker', async () => {
  await load(); const previous = globalThis.fetch; const announcementWrites = []; const featuredWrites = []; const galleryOffsets = []; let galleryReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url); const method = options.method || 'GET';
    if (value.includes('/by-slug/editor-gallery')) return response({ band: { id: 43, slug: 'editor-gallery', name: '에디터 밴드', can_manage: true, default_visibility: 'MEMBERS', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (value.endsWith('/bands/43/memberships')) return response({ memberships: [] });
    if (value.endsWith('/bands/43/invite-codes')) return response({ invite_codes: [] });
    if (value.endsWith('/bands/43/announcement') && method === 'GET') return response({ announcement: null });
    if (value.endsWith('/bands/43/announcement') && method === 'PUT') { const body = JSON.parse(options.body); announcementWrites.push(body); return response({ announcement: { id: 1, content: body.content, visibility: body.visibility, can_manage: true } }); }
    if (value.endsWith('/bands/43/gallery/featured')) return response({ gallery: null });
    if (value.includes('/bands/43/gallery?') && method === 'GET') {
      const parsed = new URL(value, 'http://localhost'); const offset = Number(parsed.searchParams.get('offset')); galleryOffsets.push(offset); galleryReads += 1;
      const startId = offset === 0 ? 1 : 21;
      return response({ gallery: Array.from({ length: offset === 0 ? 20 : 1 }, (_, index) => ({ id: startId + index, filename: `photo-${startId + index}.jpg`, url: `https://media.invalid/photo-${startId + index}.jpg${offset === 0 && startId + index === 1 && galleryReads > 1 ? '?fresh=1' : ''}`, is_featured: false })), pagination: { total: 21, has_more: offset === 0, next_offset: offset === 0 ? 20 : null } });
    }
    if (value.endsWith('/bands/43/gallery/1/featured') && method === 'PATCH') { const body = JSON.parse(options.body); featuredWrites.push(body); return response({ gallery: { id: 1, filename: 'photo-1.jpg', url: 'https://media.invalid/photo-1.jpg', is_featured: body.is_featured } }); }
    throw new Error(`unexpected ${method} ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/editor-gallery/manage', manage: true }));
    await view.findByText('에디터 밴드 관리');
    await user.click(view.getByRole('button', { name: '공지 작성' }));
    const content = view.getByLabelText('공지 내용'); assert.equal(content.maxLength, 10000);
    await user.type(content, '공개 공지'); await user.selectOptions(view.getByLabelText('공지 공개 범위'), 'PUBLIC');
    await user.click(view.getByRole('button', { name: '저장' })); await view.findByText('공개 공지');
    await user.click(view.getByRole('button', { name: '공지 수정' }));
    await user.clear(view.getByLabelText('공지 내용')); await user.type(view.getByLabelText('공지 내용'), '멤버 공지');
    await user.selectOptions(view.getByLabelText('공지 공개 범위'), 'MEMBERS');
    await user.click(view.getByRole('button', { name: '저장' })); await view.findByText('멤버 공지');
    assert.deepEqual(announcementWrites, [{ content: '공개 공지', visibility: 'PUBLIC' }, { content: '멤버 공지', visibility: 'MEMBERS' }]);

    await user.click(view.getByRole('button', { name: '갤러리에서 선택' }));
    await view.findByText('photo-1.jpg'); assert.equal(view.container.querySelectorAll('.manage-gallery-item').length, 20);
    fireEvent.error(view.getByAltText('photo-1.jpg'));
    await waitFor(() => assert.equal(view.getByAltText('photo-1.jpg').getAttribute('src'), 'https://media.invalid/photo-1.jpg?fresh=1'));
    fireEvent.error(view.getByAltText('photo-1.jpg'));
    await waitFor(() => assert.ok(view.container.querySelector('.manage-gallery-placeholder')));
    assert.equal(galleryReads, 2, 'an image refresh runs only once per workspace/image identity');
    const choose = view.getAllByRole('button', { name: '대표 사진으로 선택' })[0]; await user.click(choose);
    await view.findAllByRole('button', { name: '대표 사진 해제' });
    await user.click(view.getAllByRole('button', { name: '대표 사진 해제' })[0]);
    await waitFor(() => assert.equal(featuredWrites.length, 2));
    assert.deepEqual(featuredWrites, [{ is_featured: true }, { is_featured: false }]);
    await user.click(view.getByRole('button', { name: '사진 더 불러오기' }));
    await view.findByText('photo-21.jpg'); assert.equal(view.container.querySelectorAll('.manage-gallery-item').length, 21);
    assert.deepEqual(galleryOffsets, [0, 0, 20]); assert.equal(galleryReads, 3);
    view.unmount();
  } finally { globalThis.fetch = previous; }
});

test('a late featured-image refresh cannot restore a feature after it is cleared', async () => {
  await load(); const previous = globalThis.fetch; const lateRefresh = deferred(); let featureReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url); const method = options.method || 'GET';
    if (value.includes('/by-slug/featured-race')) return response({ band: { id: 44, slug: 'featured-race', name: '사진 밴드', can_manage: true, permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (value.endsWith('/bands/44/memberships')) return response({ memberships: [] });
    if (value.endsWith('/bands/44/invite-codes')) return response({ invite_codes: [] });
    if (value.endsWith('/bands/44/announcement')) return response({ announcement: null });
    if (value.endsWith('/bands/44/gallery/featured') && method === 'GET') { featureReads += 1; return featureReads === 1 ? response({ gallery: { id: 99, filename: 'old.jpg', url: 'https://media.invalid/old.jpg', is_featured: true } }) : lateRefresh.promise; }
    if (value.endsWith('/bands/44/gallery?limit=20&offset=0')) return response({ gallery: [{ id: 1, filename: 'new.jpg', url: 'https://media.invalid/new.jpg', is_featured: false }], pagination: { total: 1, has_more: false } });
    if (value.endsWith('/bands/44/gallery/99/featured') && method === 'PATCH') return response({ gallery: { id: 99, filename: 'old.jpg', url: 'https://media.invalid/old.jpg', is_featured: false } });
    throw new Error(`unexpected ${method} ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/featured-race/manage', manage: true }));
    await view.findByText('사진 밴드 관리'); await user.click(view.getByRole('button', { name: '갤러리에서 선택' }));
    await view.findByAltText('현재 대표 사진'); fireEvent.error(view.getByAltText('현재 대표 사진'));
    await waitFor(() => assert.equal(featureReads, 2));
    await user.click(view.getByRole('button', { name: '대표 사진 해제' }));
    await waitFor(() => assert.equal(view.queryByText('현재 대표 사진'), null));
    lateRefresh.resolve(response({ gallery: { id: 99, filename: 'old.jpg', url: 'https://media.invalid/old-fresh.jpg', is_featured: true } }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(view.queryByText('현재 대표 사진'), null);
    view.unmount();
  } finally { globalThis.fetch = previous; }
});

test('empty featured-gallery 404 does not hide a successful gallery list', async () => {
  await load(); const previous = globalThis.fetch; const featuredWrites = [];
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url);
    if (value.includes('/by-slug/no-feature')) return response({ band: { id: 45, slug: 'no-feature', name: '대표 사진 미설정', permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (value.endsWith('/bands/45/memberships')) return response({ memberships: [] });
    if (value.endsWith('/bands/45/invite-codes')) return response({ invite_codes: [{ id: 12, created_at: '2026-01-01T00:30:00Z' }] });
    if (value.endsWith('/bands/45/announcement')) return response({ announcement: null });
    if (value.endsWith('/bands/45/gallery/featured')) return response({ error: 'not found' }, 404);
    if (value.endsWith('/bands/45/gallery?limit=20&offset=0')) return response({ gallery: [{ id: 8, filename: 'available.jpg', url: 'https://media.invalid/available.jpg' }], pagination: { total: 1, has_more: false } });
    if (value.endsWith('/bands/45/gallery/8/featured') && options.method === 'PATCH') {
      const body = JSON.parse(options.body); featuredWrites.push(body);
      return response({ gallery: { id: 8, filename: 'available.jpg', url: 'https://media.invalid/available.jpg', is_featured: body.is_featured } });
    }
    throw new Error(`unexpected ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/no-feature/manage', manage: true }));
    await view.findByText('2026-01-01 09:30 KST');
    await user.click(await view.findByRole('button', { name: '갤러리에서 선택' }));
    await view.findByText('available.jpg');
    assert.equal(view.queryByText('갤러리를 불러오지 못했습니다.'), null);
    await user.click(view.getByRole('button', { name: '대표 사진으로 선택' }));
    await view.findAllByRole('button', { name: '대표 사진 해제' });
    assert.deepEqual(featuredWrites, [{ is_featured: true }]);
    view.unmount();
  } finally { globalThis.fetch = previous; }
});

test('gallery list 404 remains inaccessible while featured-only 404 is optional and 500 stays an error', async () => {
  await load(); const previous = globalThis.fetch; let mode = 'list-404';
  globalThis.fetch = async (url) => {
    const value = String(url);
    if (value.includes('/by-slug/gallery-errors')) return response({ band: { id: 46, slug: 'gallery-errors', name: '갤러리 오류 밴드', permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (value.endsWith('/bands/46/memberships')) return response({ memberships: [] });
    if (value.endsWith('/bands/46/invite-codes')) return response({ invite_codes: [] });
    if (value.endsWith('/bands/46/announcement')) return response({ announcement: null });
    if (value.endsWith('/bands/46/gallery/featured')) return mode === 'list-404' ? response({ error: 'not found' }, 404) : response({ error: 'unavailable' }, 500);
    if (value.endsWith('/bands/46/gallery?limit=20&offset=0')) return mode === 'list-404'
      ? response({ error: 'not found' }, 404)
      : response({ gallery: [], pagination: { total: 0, has_more: false } });
    throw new Error(`unexpected ${value}`);
  };
  try {
    const missingList = render(createElement(App, { entry: '/bands/gallery-errors/manage', manage: true }));
    await user.click(await missingList.findByRole('button', { name: '갤러리에서 선택' }));
    await missingList.findByText('찾을 수 없는 관리 공간입니다.');
    missingList.unmount();

    mode = 'featured-500';
    const failedFeature = render(createElement(App, { entry: '/bands/gallery-errors/manage', manage: true }));
    await user.click(await failedFeature.findByRole('button', { name: '갤러리에서 선택' }));
    await failedFeature.findByText('갤러리를 불러오지 못했습니다.');
    assert.ok(failedFeature.getByText('갤러리 오류 밴드 관리'));
    failedFeature.unmount();
  } finally { globalThis.fetch = previous; }
});

test('announcement 403 clears privileged management controls and late save cannot repaint after account switch', async () => {
  await load(); const previous = globalThis.fetch; const oldSave = deferred(); let phase = 'stale';
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url); const method = options.method || 'GET'; const token = options.headers?.Authorization;
    if (value.includes('/by-slug/guarded')) return response({ band: { id: token === 'Bearer u2' ? 2 : 1, slug: 'guarded', name: token === 'Bearer u2' ? 'B 밴드' : 'A 밴드', can_manage: true, permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (value.endsWith('/memberships')) return response({ memberships: [] });
    if (value.endsWith('/invite-codes')) return response({ invite_codes: [] });
    if (value.endsWith('/announcement') && method === 'GET') return response({ announcement: { id: 1, content: token === 'Bearer u2' ? 'B 공지' : 'A 공지', visibility: 'PUBLIC', can_manage: true } });
    if (value.endsWith('/announcement') && method === 'PUT') return phase === 'stale' ? oldSave.promise : response({}, 403);
    throw new Error(`unexpected ${method} ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/guarded/manage', manage: true, authValue: account('u1') }));
    await view.findByText('A 공지'); await user.click(view.getByRole('button', { name: '공지 수정' }));
    await user.clear(view.getByLabelText('공지 내용')); await user.type(view.getByLabelText('공지 내용'), 'A 저장 대기'); await user.click(view.getByRole('button', { name: '저장' }));
    view.rerender(createElement(App, { entry: '/bands/guarded/manage', manage: true, authValue: account('u2') }));
    await view.findByText('B 공지');
    await act(async () => { oldSave.resolve(response({ announcement: { id: 1, content: '늦은 A 저장', visibility: 'PUBLIC', can_manage: true } })); });
    assert.equal(view.queryByText('늦은 A 저장'), null); assert.ok(view.getByText('B 공지'));
    phase = 'forbidden'; await user.click(view.getByRole('button', { name: '공지 수정' }));
    await user.clear(view.getByLabelText('공지 내용')); await user.type(view.getByLabelText('공지 내용'), '403 테스트'); await user.click(view.getByRole('button', { name: '저장' }));
    await view.findByText('찾을 수 없는 관리 공간입니다.');
    assert.equal(view.queryByRole('button', { name: '공지 수정' }), null); assert.equal(view.queryByRole('button', { name: '새 초대 코드 만들기' }), null);
    view.unmount();
  } finally { globalThis.fetch = previous; }
});
