import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement, StrictMode, useEffect } from 'react';
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
const load = async () => { if (onboarding) return; vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); onboarding = await vite.ssrLoadModule('/src/components/bands/BandOnboarding.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); bands = await vite.ssrLoadModule('/src/context/BandContext.jsx'); };
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

test('owner roster shows only server-authorized removal and updates locally without a fragile refetch', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; window.confirm = () => true; const writes = []; let rosterReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url);
    if (options.method === 'POST') { writes.push([path, JSON.parse(options.body)]); return response({ changed: true, membership: { id: 2, band_id: 1, status: 'left' } }); }
    if (path.includes('/by-slug/a')) return response({ band: { id: 1, slug: 'a', name: 'A', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (path.endsWith('/memberships')) { rosterReads += 1; return response({ memberships: [{ id: 1, band_id: 1, status: 'active', role: 'owner', member_name: 'Owner', can_remove_from_band: false }, { id: 2, band_id: 1, status: 'active', role: 'member', member_name: '밴드 이름', user: { display_name: '계정 이름' }, can_remove_from_band: true }] }); }
    return response({ invite_links: [], announcement: null });
  };
  try {
    const view = render(createElement(App, { entry: '/bands/a/manage', manage: true })); await view.findByRole('button', { name: '밴드 이름 추방' }); assert.equal(view.queryByRole('button', { name: 'Owner 추방' }), null);
    // Failed waitFor polls must not inspect a React DOM node (and its fiber graph).
    fireEvent.click(view.getByRole('button', { name: '밴드 이름 추방' })); await waitFor(() => assert.equal(view.queryByText('밴드 이름 · member') === null, true)); assert.deepEqual(writes.map(([path, body]) => [path.replace(/^https?:\/\/[^/]+/, ''), body]), [['/api/v2/bands/1/memberships/2/remove', { confirm: true }]]); assert.equal(rosterReads, 1);
    fireEvent.click(view.getByRole('button', { name: '명단 새로고침' })); await waitFor(() => assert.equal(rosterReads, 2));
  } finally { globalThis.fetch = previousFetch; window.confirm = previousConfirm; }
});

test('ordinary band administrator does not get owner removal controls', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async (url) => String(url).includes('/by-slug/a') ? response({ band: { id: 1, slug: 'a', name: 'A', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } }) : response({ memberships: [{ id: 2, status: 'active', role: 'member', member_name: '멤버', can_remove_from_band: false }], invite_links: [], announcement: null });
  try { const view = render(createElement(App, { entry: '/bands/a/manage', manage: true })); await view.findByText('A 관리'); assert.equal(view.queryByText('멤버 역할'), null); assert.equal(view.queryByRole('button', { name: /추방$/ }), null); }
  finally { globalThis.fetch = previous; }
});

test('revoking an invite immediately removes it from the manager list without a full-page reload', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; window.confirm = () => true;
  let inviteReads = 0; let pageOffset = null; const revokedAt = '2026-10-23T00:00:00Z';
  const activeInvite = { id: 44, band_id: 1, created_at: '2026-10-22T00:00:00Z', expires_at: '2099-01-01T00:00:00Z', revoked_at: null };
  const initialInvites = [{ id: 43, band_id: 1, created_at: '2026-10-21T00:00:00Z', expires_at: '2099-01-01T00:00:00Z', revoked_at: revokedAt }, activeInvite, ...Array.from({ length: 18 }, (_, index) => ({ id: 50 + index, band_id: 1, created_at: '2026-10-20T00:00:00Z', expires_at: '2099-01-01T00:00:00Z', revoked_at: null }))];
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith('/by-slug/a')) return response({ band: { id: 1, slug: 'a', name: 'A', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (path.includes('/bands/1/invite-links') && options.method === 'POST') return response({ invite_link: { ...activeInvite, revoked_at: revokedAt } });
    if (path.includes('/bands/1/invite-links')) {
      inviteReads += 1;
      if (path.includes('offset=20')) { pageOffset = 20; return response({ invite_links: [{ id: 100, band_id: 1, created_at: '2026-10-19T00:00:00Z', expires_at: '2099-01-01T00:00:00Z', revoked_at: null }], pagination: { limit: 20, offset: 20, total: 21, has_more: false } }); }
      return response({ invite_links: initialInvites, pagination: { limit: 20, offset: 0, total: 21, has_more: true } });
    }
    if (path.endsWith('/memberships')) return response({ memberships: [] });
    if (path.endsWith('/announcement')) return response({ announcement: null });
    throw new Error(`unexpected ${path}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/a/manage', manage: true }));
    await waitFor(() => assert.equal(view.getAllByText(/사용 가능 · 발급/).length, 19));
    assert.equal(view.getAllByText(/사용 가능 · 발급/).length, 19);
    assert.equal(view.queryByText(/2026-10-21/), null);
    const activeRow = view.getAllByRole('listitem').find((item) => item.textContent.includes('2026-10-22'));
    assert.ok(activeRow);
    await userEvent.click(within(activeRow).getByRole('button', { name: '폐기' }));
    await waitFor(() => assert.equal(view.queryByText(/2026-10-22/), null));
    assert.equal(view.queryByText(/2026-10-22/), null);
    assert.equal(view.getByRole('button', { name: '이전 초대 링크 더 보기' }) !== null, true);
    await userEvent.click(view.getByRole('button', { name: '이전 초대 링크 더 보기' }));
    await waitFor(() => assert.equal(view.queryByText(/2026-10-19/) !== null, true));
    assert.equal(pageOffset, 20);
    assert.equal(inviteReads, 2);
  } finally { globalThis.fetch = previousFetch; window.confirm = previousConfirm; }
});

test('visitor discover preserves a safe login return and join becomes pending once', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, request = {}) => { calls.push([String(url), request]); return response({ bands: [{ id: 7, slug: 'public', name: 'Public' }], pagination: { total: 1, has_more: false } }); };
  try { const visitor = render(createElement(App, { entry: '/bands/discover', authValue: account(null), discover: true })); await visitor.findByText('Public'); await userEvent.click(visitor.getByRole('button', { name: '로그인 후 가입 요청' })); await visitor.findByText('login:?returnTo=%2Fbands%2Fdiscover'); assert.ok(calls.every(([url]) => !url.includes('invite'))); visitor.unmount();
    let joined = 0; let membershipReads = 0; globalThis.fetch = async (url) => { if (String(url).endsWith('/api/v2/bands')) { membershipReads += 1; return response({ bands: [], pending_memberships: membershipReads > 1 ? [{ id: 1, band: { id: 7 } }] : [] }); } if (String(url).includes('join-requests')) { joined += 1; return response({ membership: { id: 1 }, created: true }, 201); } return response({ bands: [{ id: 7, slug: 'public', name: 'Public' }], pagination: { total: 1, has_more: false } }); };
    const member = render(createElement(App, { entry: '/bands/discover', discover: true })); await member.findByText('Public'); const join = member.getByRole('button', { name: '가입 요청' }); await userEvent.click(join); assert.equal(joined, 0); assert.equal(member.getByRole('button', { name: '가입 요청 보내기' }).disabled, true); await user.type(member.getByLabelText('밴드에서 사용할 이름 (필수)'), '멤버 이름'); await userEvent.click(member.getByRole('button', { name: '가입 요청 보내기' })); await waitFor(() => assert.equal(joined, 1)); await member.findByRole('button', { name: '승인 대기 중' }); await userEvent.click(member.getByRole('button', { name: '승인 대기 중' })); assert.equal(joined, 1);
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

test('account page replaces retired invite-code entry with received-link guidance and performs no write', async () => {
  await load(); const previous = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => { writes.push([String(url), options]); return response({}); };
  try { const view = render(createElement(auth.AuthContext.Provider, { value: account() }, createElement(onboarding.InviteLinkGuidance))); await view.findByText(/밴드 관리자가 보낸 초대 링크를 열어/); assert.equal(view.queryByLabelText('초대 코드'), null); assert.equal(view.queryByRole('button'), null); assert.equal(writes.length, 0); } finally { globalThis.fetch = previous; }
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
    await user.type(view.getByLabelText('밴드에서 사용할 이름 (필수)'), '  요청 이름  ');
    const input = view.getByLabelText('간단한 자기소개 (선택)');
    fireEvent.change(input, { target: { value: 'a'.repeat(301) } });
    // Use the native input event path for controlled textareas in jsdom.
    await userEvent.type(input, ' 기타와 보컬을 해요. ');
    const send = view.getByRole('button', { name: '가입 요청 보내기' });
    assert.equal(send.disabled, true); await user.clear(input); await userEvent.type(input, ' 기타와 보컬을 해요. ');
    await userEvent.click(send); fireEvent.submit(input.closest('form')); assert.equal(writes.length, 1);
    assert.deepEqual(writes[0], { name: '요청 이름', introduction: '기타와 보컬을 해요.' });
    await act(async () => pending.resolve(response({ membership: { id: 1 }, created: true }, 201)));
    await waitFor(() => assert.equal(view.queryByLabelText('간단한 자기소개 (선택)') === null, true));
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
    await user.type(view.getByLabelText('밴드에서 사용할 이름 (필수)'), 'private name');
    await userEvent.type(view.getByLabelText('간단한 자기소개 (선택)'), 'private draft'); await userEvent.click(view.getByRole('button', { name: '가입 요청 보내기' }));
    view.rerender(createElement(App, { entry: '/bands/discover', discover: true, authValue: account('b') }));
    assert.equal(view.queryByDisplayValue('private draft'), null);
    assert.equal(view.queryByDisplayValue('private name'), null);
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
    return response({ invite_links: [] });
  };
  try {
    const view = render(createElement(App, { entry: '/bands/a/manage', manage: true })); await view.findByText('Applicant');
    assert.ok(view.getByText('2026-10-17 09:30 KST'));
    const text = view.container.querySelector('.membership-introduction'); assert.equal(text.textContent, '<img src=x onerror=alert(1)>\n기타 연주');
    assert.equal(text.querySelector('img'), null); assert.equal(view.queryByText('must-not-display'), null);
  } finally { globalThis.fetch = previous; }
});

test('required member name is bounded, retained after a failed request and cleared for another band', async () => {
  await load(); const previous = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/api/v2/bands')) return response({ bands: [], pending_memberships: [] });
    if (String(url).includes('join-requests')) { writes.push(JSON.parse(options.body)); return response({}, 400); }
    return response({ bands: [{ id: 7, slug: 'public', name: 'Public' }, { id: 8, slug: 'other', name: 'Other' }], pagination: { total: 2, has_more: false } });
  };
  try {
    const view = render(createElement(App, { entry: '/bands/discover', discover: true }));
    await view.findAllByRole('button', { name: '가입 요청' }); await user.click(view.getAllByRole('button', { name: '가입 요청' })[0]);
    const name = view.getByLabelText('밴드에서 사용할 이름 (필수)'); const send = view.getByRole('button', { name: '가입 요청 보내기' });
    assert.equal(view.getByText('승인 후 멤버 목록에 표시할 이름을 100자 이내로 입력해 주세요.').id, 'join-name-help');
    assert.equal(view.queryByText(/기존에 연결된 멤버 프로필이 있다면 이름은 유지됩니다/), null);
    assert.equal(name.required, true); assert.equal(name.maxLength, 100); assert.equal(send.disabled, true);
    await user.type(name, '  '); assert.equal(send.disabled, true); await user.clear(name);
    await user.type(name, 'x'.repeat(101)); assert.equal(name.value.length, 100); await user.clear(name);
    await user.type(name, '  무대 이름  '); await user.click(send); await view.findByRole('alert');
    assert.equal(view.getByLabelText('밴드에서 사용할 이름 (필수)').value, '  무대 이름  '); assert.equal(send.disabled, false);
    assert.deepEqual(writes, [{ name: '무대 이름', introduction: '' }]);
    await user.click(view.getAllByRole('button', { name: '가입 요청' })[1]);
    assert.equal(view.getByLabelText('밴드에서 사용할 이름 (필수)').value, ''); assert.equal(view.queryByRole('alert'), null);
  } finally { globalThis.fetch = previous; }
});

test('manager sees the requested member name with legacy account-name fallback', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const path = String(url);
    if (path.includes('/by-slug/a')) return response({ band: { id: 1, slug: 'a', name: 'A', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (path.endsWith('/memberships')) return response({ memberships: [{ id: 1, status: 'pending', requested_name: '<멤버 이름>', user: { display_name: 'Hidden account name' } }, { id: 2, status: 'pending', requested_name: null, user: { display_name: 'Legacy account name' } }] });
    return response({ invite_links: [], announcement: null });
  };
  try { const view = render(createElement(App, { entry: '/bands/a/manage', manage: true })); await view.findByText('<멤버 이름>'); assert.ok(view.getByText('Legacy account name')); assert.equal(view.queryByText('Hidden account name'), null); }
  finally { globalThis.fetch = previous; }
});

test('creation form has one in-flight submit and reports its status', async () => {
  await load(); const previous = globalThis.fetch; const first = deferred(); let writes = 0;
  globalThis.fetch = async (url) => { if (String(url).includes('band-slugs/availability')) return response({ slug: 'new-band', available: true }); if (String(url).includes('band-creation-requests')) { writes += 1; return first.promise; } throw new Error('unexpected'); };
  try { const view = render(createElement(App, { entry: '/bands/request', create: true })); await userEvent.type(view.getByLabelText('밴드 이름'), '새 밴드'); await userEvent.type(view.getByLabelText('밴드 영어명'), 'new-band'); await userEvent.click(view.getByRole('button', { name: '중복 확인' })); await view.findByText('사용 가능한 주소입니다.'); const button = view.getByRole('button', { name: '생성 요청 보내기' }); await userEvent.click(button); await userEvent.click(button); assert.equal(writes, 1); await act(async () => first.resolve(response({ request: { name: '새 밴드', status: 'pending' } }, 201))); await view.findByText(/승인 대기 중/); } finally { globalThis.fetch = previous; }
});

test('slug availability is explicit, edit invalidates stale replies, and preview uses the current origin', async () => {
  await load(); const previous = globalThis.fetch; const checks = [];
  globalThis.fetch = async (url, options = {}) => { const wait = deferred(); checks.push({ url: String(url), options, wait }); return wait.promise; };
  try {
    const view = render(createElement(App, { entry: '/bands/request', create: true })); const input = view.getByLabelText('밴드 영어명');
    assert.match(view.getByText(/생성될 주소/).textContent, /올바른 영어명을 입력/); assert.equal(view.getByRole('button', { name: '생성 요청 보내기' }).disabled, true);
    await userEvent.type(input, 'first-band'); assert.match(view.getByText(/생성될 주소/).textContent, /http:\/\/localhost\/bands\/first-band/); await userEvent.click(view.getByRole('button', { name: '중복 확인' }));
    assert.match(checks[0].url, /\/api\/v2\/band-slugs\/availability\?slug=first-band$/); assert.ok(checks[0].options.headers?.Authorization);
    await user.click(input); await user.clear(input); await user.type(input, 'second-band');
    await act(async () => checks[0].wait.resolve(response({ slug: 'first-band', available: true })));
    assert.equal(view.queryByText('사용 가능한 주소입니다.'), null); assert.equal(view.getByRole('button', { name: '생성 요청 보내기' }).disabled, true);
    await userEvent.click(view.getByRole('button', { name: '중복 확인' }));
    await act(async () => checks[1].wait.resolve(response({ slug: 'second-band', available: false })));
    await view.findByText('이미 사용 중인 주소입니다.'); assert.equal(view.getByRole('button', { name: '생성 요청 보내기' }).disabled, true);
  } finally { globalThis.fetch = previous; }
});

test('maximum-length slug preview keeps its wrapping class and remains a single mobile-safe form item', async () => {
  await load(); const view = render(createElement(App, { entry: '/bands/request', create: true }));
  const input = view.getByLabelText('밴드 영어명'); await user.type(input, 'a'.repeat(80));
  const preview = view.getByText(/생성될 주소/);
  assert.ok(preview.classList.contains('band-request-url-preview'));
  const bandsCss = readFileSync(new URL('./BandsPage.css', import.meta.url), 'utf8');
  assert.match(bandsCss, /\.bands-form > \.band-request-url-preview\s*\{[^}]*min-width:\s*0;[^}]*overflow-wrap:\s*anywhere;/);
  assert.equal(preview.textContent, `생성될 주소(예시): http://localhost/bands/${'a'.repeat(80)}`);
  assert.equal(input.value.length, 80); assert.equal(view.getByRole('button', { name: '중복 확인' }).disabled, false);
});

test('StrictMode availability resolves successfully and a separate unfinished check aborts on unmount', async () => {
  await load(); const previous = globalThis.fetch; const available = deferred(); let strictSignal; let unmountSignal;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).includes('band-slugs/availability')) {
      if (new URL(String(url), 'http://localhost').searchParams.get('slug') === 'strict-band') { strictSignal = options.signal; return available.promise; }
      unmountSignal = options.signal; return new Promise(() => {});
    }
    throw new Error('unexpected');
  };
  try {
    const view = render(createElement(StrictMode, null, createElement(App, { entry: '/bands/request', create: true })));
    const input = view.getByLabelText('밴드 영어명'); await user.type(input, 'strict-band'); await user.click(view.getByRole('button', { name: '중복 확인' }));
    await view.findByText('주소 사용 여부를 확인하고 있습니다…'); assert.equal(strictSignal.aborted, false);
    await act(async () => available.resolve(response({ slug: 'strict-band', available: true })));
    await view.findByText('사용 가능한 주소입니다.'); assert.equal(view.getByRole('button', { name: '생성 요청 보내기' }).disabled, false);
    view.unmount();

    const unfinished = render(createElement(App, { entry: '/bands/request', create: true }));
    await user.type(unfinished.getByLabelText('밴드 영어명'), 'unfinished'); await user.click(unfinished.getByRole('button', { name: '중복 확인' }));
    await unfinished.findByText('주소 사용 여부를 확인하고 있습니다…'); unfinished.unmount(); assert.equal(unmountSignal.aborted, true);
  } finally { globalThis.fetch = previous; }
});

test('reserved service routes cannot pass availability check or produce a valid URL preview', async () => {
  await load(); const previous = globalThis.fetch; let checks = 0;
  globalThis.fetch = async () => { checks += 1; return response({ slug: 'discover', available: true }); };
  try {
    const view = render(createElement(App, { entry: '/bands/request', create: true })); const input = view.getByLabelText('밴드 영어명');
    for (const slug of ['discover', 'request']) {
      await user.type(input, slug);
      assert.ok(view.getByRole('alert').textContent.includes('서비스 페이지 주소'));
      assert.match(view.getByText(/생성될 주소/).textContent, /올바른 영어명을 입력/);
      assert.equal(view.getByRole('button', { name: '중복 확인' }).disabled, true);
      assert.equal(view.getByRole('button', { name: '생성 요청 보내기' }).disabled, true);
      await user.clear(input);
    }
    assert.equal(checks, 0);
  } finally { globalThis.fetch = previous; }
});

test('creation request state is cleared on account switch and late prior responses stay isolated', async () => {
  await load(); const previous = globalThis.fetch; const write = deferred(); let writes = 0;
  globalThis.fetch = async (url) => {
    if (String(url).includes('band-slugs/availability')) return response({ slug: 'same-slug', available: true });
    if (String(url).includes('band-creation-requests')) { writes += 1; return write.promise; }
    throw new Error('unexpected');
  };
  try {
    const view = render(createElement(App, { entry: '/bands/request', create: true, authValue: account('a') }));
    await user.type(view.getByLabelText('밴드 이름'), 'A 이름'); await user.type(view.getByLabelText('밴드 영어명'), 'same-slug'); await user.click(view.getByRole('button', { name: '중복 확인' })); await view.findByText('사용 가능한 주소입니다.');
    await user.click(view.getByRole('button', { name: '생성 요청 보내기' })); await waitFor(() => assert.equal(writes, 1));
    view.rerender(createElement(App, { entry: '/bands/request', create: true, authValue: account('b') }));
    assert.equal(view.getByLabelText('밴드 이름').value, ''); assert.equal(view.getByLabelText('밴드 영어명').value, '');
    await act(async () => write.resolve(response({ request: { name: 'A 이름', status: 'pending' } }, 201)));
    assert.equal(view.queryByText('사용 가능한 주소입니다.'), null); assert.equal(view.getByRole('button', { name: '생성 요청 보내기' }).disabled, true);
    assert.equal(view.queryByText(/A 이름.*승인 대기 중/), null); view.unmount();
  } finally { globalThis.fetch = previous; }
});

test('account switch aborts a creation request waiting for its access token before fetch', async () => {
  await load(); const previous = globalThis.fetch; const delayedToken = deferred(); let tokenReads = 0; let availabilityReads = 0; let writes = 0;
  const accountA = { ...account('a'), getAccessToken: async () => { tokenReads += 1; return tokenReads === 1 ? 'token-a' : delayedToken.promise; } };
  globalThis.fetch = async (url) => {
    const path = String(url);
    if (path.includes('band-slugs/availability')) { availabilityReads += 1; return response({ slug: 'token-race', available: true }); }
    if (path.includes('band-creation-requests')) { writes += 1; return response({ request: { name: 'A 이름', status: 'pending' } }, 201); }
    throw new Error('unexpected');
  };
  try {
    const view = render(createElement(App, { entry: '/bands/request', create: true, authValue: accountA }));
    await user.type(view.getByLabelText('밴드 이름'), 'A 이름'); await user.type(view.getByLabelText('밴드 영어명'), 'token-race');
    await user.click(view.getByRole('button', { name: '중복 확인' })); await view.findByText('사용 가능한 주소입니다.'); assert.equal(availabilityReads, 1);
    await user.click(view.getByRole('button', { name: '생성 요청 보내기' })); await waitFor(() => assert.equal(tokenReads, 2));
    view.rerender(createElement(App, { entry: '/bands/request', create: true, authValue: account('b') }));
    await act(async () => delayedToken.resolve('token-a')); await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(writes, 0); assert.equal(view.getByLabelText('밴드 이름').value, ''); assert.equal(view.queryByText(/A 이름.*승인 대기 중/), null);
    view.unmount();
  } finally { globalThis.fetch = previous; }
});

test('availability errors can be retried and a server conflict blocks a stale availability result', async () => {
  await load(); const previous = globalThis.fetch; let checks = 0; let writes = 0;
  globalThis.fetch = async (url) => { const path = String(url); if (path.includes('band-slugs/availability')) { checks += 1; return checks === 1 ? response({ error: 'unavailable' }, 500) : response({ slug: 'club-name', available: true }); } if (path.includes('band-creation-requests')) { writes += 1; return response({ error: 'taken' }, 409); } throw new Error('unexpected'); };
  try {
    const view = render(createElement(App, { entry: '/bands/request', create: true })); const input = view.getByLabelText('밴드 영어명'); await userEvent.type(view.getByLabelText('밴드 이름'), '밴드'); await userEvent.type(input, 'club-name');
    await userEvent.click(view.getByRole('button', { name: '중복 확인' })); await view.findByText(/주소 사용 여부를 확인하지 못했습니다/);
    await userEvent.click(view.getByRole('button', { name: '중복 확인' })); await view.findByText('사용 가능한 주소입니다.');
    await userEvent.click(view.getByRole('button', { name: '생성 요청 보내기' })); await view.findByRole('alert');
    assert.equal(writes, 1); assert.equal(view.getByRole('button', { name: '생성 요청 보내기' }).disabled, true);
    await user.click(view.getByRole('button', { name: '중복 확인' })); await view.findByText('사용 가능한 주소입니다.');
    assert.equal(view.getByRole('button', { name: '생성 요청 보내기' }).disabled, false);
  } finally { globalThis.fetch = previous; }
});

test('creation request uses the shared return-link styling and explains the English URL name without weakening slug validation', async () => {
  await load(); const view = render(createElement(App, { entry: '/bands/request', create: true }));
  const back = view.getByRole('link', { name: '내 밴드' }); assert.equal(back.getAttribute('href'), '/bands'); assert.ok(back.classList.contains('bands-return-link')); assert.equal(back.tabIndex, 0); await user.tab(); assert.equal(document.activeElement === back, true);
  const input = view.getByLabelText('밴드 영어명'); assert.equal(input.name, 'slug'); assert.equal(input.required, true); assert.equal(input.maxLength, 80); assert.equal(input.getAttribute('autoCapitalize'), 'none'); assert.equal(input.getAttribute('pattern'), '[a-z0-9]+(-[a-z0-9]+)*');
  const help = document.getElementById(input.getAttribute('aria-describedby')); assert.match(help.textContent, /해당 이름이 밴드 URL 주소가 됩니다/); assert.match(help.textContent, /영문 소문자·숫자/); assert.match(help.textContent, /하이픈/); assert.match(help.textContent, /공백·대문자는 사용할 수 없/); assert.match(help.textContent, /80자/);
  for (const value of ['new-band', 'band123', 'my-2-band']) { fireEvent.change(input, { target: { value } }); assert.equal(input.checkValidity(), true, value); }
  for (const value of ['New-Band', 'new band', '-band', 'band-', 'new--band']) { fireEvent.change(input, { target: { value } }); assert.equal(input.checkValidity(), false, value); }
});

test('management hides non-manager and ignores a late A membership decision after route switch', async () => {
  await load(); const previous = globalThis.fetch; const decision = deferred(); const aDetail = { band: { id: 1, slug: 'a', name: 'A', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } }; const bDetail = { band: { id: 2, slug: 'b', name: 'B', membership: { role: 'admin' }, permissions: { can_manage_members: true, can_manage_invites: true } } };
  globalThis.fetch = async (url, options = {}) => { const value = String(url); if (value.includes('/by-slug/a')) return response(aDetail); if (value.includes('/by-slug/b')) return response(bDetail); if (value.endsWith('/announcement')) return response({ announcement: null }); if (value.includes('/bands/1/memberships')) return response({ memberships: [{ id: 12, status: 'pending', user: { display_name: 'A 요청' } }] }); if (value.includes('/bands/2/memberships')) return response({ memberships: [] }); if (value.includes('/invite-links') && options.method !== 'POST') return response({ invite_links: [] }); if (value.includes('/memberships/12')) return decision.promise; throw new Error(`unexpected ${value}`); };
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
    if (value.includes('/bands/1/invite-links') && options.method === 'POST') return response({ invite_link: { id: 91, band_id: 1, created_at: '2026-01-01T00:00:00Z', expires_at: '2099-01-02T00:00:00Z', revoked_at: null }, token: 'A'.repeat(43) }, 201);
    if (value.includes('/bands/1/invite-links')) return response({ invite_links: [] });
    if (value.includes('/bands/2/invite-links')) return response({ invite_links: [] });
    if (value.includes('/memberships/11')) return response({}, 500);
    throw new Error(`unexpected ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/club/manage', manage: true, authValue: account('a') }));
    await view.findByText('A 요청');
    await userEvent.click(view.getByRole('button', { name: '새 초대 링크 만들기' }));
    await view.findByDisplayValue(/invite#token=A{43}/);
    await userEvent.click(view.getByRole('button', { name: '승인' }));
    await view.findByRole('alert');

    // The same MemoryRouter remains mounted; changing only the provider value
    // must not leave A's privileged state painted while B is still loading.
    view.rerender(createElement(App, { entry: '/bands/club/manage', manage: true, authValue: account('b') }));
    assert.equal(view.queryByDisplayValue(/invite#token=A{43}/), null);
    assert.equal(view.queryByText('A 요청'), null);
    assert.equal(view.queryByRole('alert'), null);
    assert.equal(view.queryByRole('button', { name: '승인' }), null);
    assert.equal(view.queryByRole('button', { name: '새 초대 링크 만들기' }), null);

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
    if (value.includes('/bands/1/invite-links')) return aInvites.promise;
    if (value.includes('/bands/2/memberships')) return response({ memberships: [{ id: 22, status: 'pending', user: { display_name: 'B 요청' } }] });
    if (value.includes('/bands/2/invite-links')) return response({ invite_links: [] });
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
    if (value.includes('/bands/1/invite-links') && options.method === 'POST') return create.promise;
    if (value.includes('/bands/1/invite-links')) {
      inviteReads += 1;
      if (inviteReads === 1) return response({ invite_links: [] });
      followupSignals.push(options.signal); return inviteReads === 2 ? oldInvites.promise : newInvites.promise;
    }
    if (value.includes('/memberships/12')) return approve.promise;
    throw new Error(`unexpected ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/club/manage', manage: true }));
    await view.findByText('기존 요청');
    await userEvent.click(view.getByRole('button', { name: '새 초대 링크 만들기' }));
    await userEvent.click(view.getByRole('button', { name: '승인' }));

    await act(async () => create.resolve(response({ invite_link: { id: 92, band_id: 1, created_at: '2026-01-01T00:00:00Z', expires_at: '2099-01-02T00:00:00Z', revoked_at: null }, token: 'B'.repeat(43) }, 201)));
    await waitFor(() => assert.equal(membershipReads, 2));
    await act(async () => approve.resolve(response({ membership: { id: 12 } })));
    await waitFor(() => assert.equal(membershipReads, 3));
    assert.ok(followupSignals.slice(0, 2).every((signal) => signal.aborted), 'the earlier refetch is aborted before its ignored responses resolve');

    await act(async () => { newMembers.resolve(response({ memberships: [{ id: 14, status: 'pending', user: { display_name: 'New' } }] })); newInvites.resolve(response({ invite_links: [] })); });
    await view.findByText('New');
    await act(async () => { oldMembers.resolve(response({ memberships: [{ id: 13, status: 'pending', user: { display_name: 'Old' } }] })); oldInvites.resolve(response({ invite_links: [] })); });
    await new Promise((done) => setTimeout(done, 0));
    assert.equal(view.queryByText('Old'), null);
    assert.equal(view.queryByText('New')?.textContent, 'New');
  } finally { globalThis.fetch = previous; }
});

test('management return control is a keyboard-focusable canonical RouterLink', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async (url) => { const value = String(url);
    if (value.includes('/by-slug/requested')) return response({ band: { id: 88, slug: 'canonical-band', name: '이름이 긴 밴드', permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (value.includes('/bands/88/memberships')) return response({ memberships: [] }); if (value.includes('/bands/88/invite-links')) return response({ invite_links: [] }); if (value.includes('/bands/88/announcement')) return response({ announcement: null });
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
    if (path.includes('/invite-links')) return response({ invite_links: [] });
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
    if (value.includes('/bands/51/memberships')) return response({ memberships: [] });
    if (value.includes('/bands/51/invite-links')) return response({ invite_links: [] });
    if (value.includes('/bands/51/announcement')) return response({ announcement: null });
    if (value.includes('/bands/51/profile') && method === 'PATCH') { const body = JSON.parse(options.body); writes.push(body); profileDescription = body.description.trim() || null; owner.description = profileDescription; return response({ band: { ...owner, description: profileDescription } }); }
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
    globalThis.fetch = async (url) => String(url).endsWith('/by-slug/profile') ? response({ band: admin }) : String(url).endsWith('/memberships') ? response({ memberships: [] }) : String(url).includes('/invite-links') ? response({ invite_links: [] }) : response({ announcement: null });
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
    if (value.includes('/bands/61/memberships')) return response({ memberships: [] }); if (value.includes('/bands/61/invite-links')) return response({ invite_links: [] }); if (value.includes('/bands/61/announcement')) return response({ announcement: null });
    if (value.includes('/bands/61/profile') && method === 'PATCH') { profileWrite += 1; return response({}, 403); }
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
    if (value.includes('/bands/62/memberships')) return response({ memberships: [] }); if (value.includes('/bands/62/invite-links')) return response({ invite_links: [] }); if (value.includes('/bands/62/announcement')) return response({ announcement: null });
    if (value.includes('/bands/62/profile') && method === 'PATCH') return response({}, 404);
    throw new Error(`unexpected ${method} ${value}`);
  };
  try { const view = render(createElement(App, { entry: '/bands/deleted/manage', manage: true })); await view.findByText('삭제된 밴드 관리'); await user.click(view.getByRole('button', { name: '소개 수정' })); await user.type(view.getByLabelText('소개'), 'new'); await user.click(view.getByRole('button', { name: '소개 저장' })); await view.findByText('찾을 수 없는 관리 공간입니다.'); assert.equal(view.queryByText('삭제된 밴드 관리'), null); assert.equal(view.queryByRole('button', { name: '새 초대 링크 만들기' }), null); assert.equal(detailReads, 2); }
  finally { globalThis.fetch = previous; }
});

test('profile save failure keeps the draft available for retry', async () => {
  await load(); const previous = globalThis.fetch; let writes = 0;
  const band = { band: { id: 63, slug: 'retry', name: '재시도 밴드', description: '', membership: { role: 'owner' }, permissions: { can_manage_members: true, can_manage_invites: true, can_edit_band_profile: true } } };
  globalThis.fetch = async (url, options = {}) => { const value = String(url); const method = options.method || 'GET';
    if (value.endsWith('/by-slug/retry')) return response(band); if (value.includes('/bands/63/memberships')) return response({ memberships: [] }); if (value.includes('/bands/63/invite-links')) return response({ invite_links: [] }); if (value.includes('/bands/63/announcement')) return response({ announcement: null });
    if (value.includes('/bands/63/profile') && method === 'PATCH') { writes += 1; return response({}, 500); }
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
    if (value.includes('/bands/71/memberships') || value.includes('/bands/72/memberships')) return response({ memberships: [] });
    if (value.includes('/bands/71/invite-links') || value.includes('/bands/72/invite-links')) return response({ invite_links: [] });
    if (value.includes('/bands/71/announcement') || value.includes('/bands/72/announcement')) return response({ announcement: null });
    if (value.includes('/bands/71/profile') && options.method === 'PATCH') { writes += 1; return save.promise; }
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
    if (value.includes('/bands/81/memberships') || value.includes('/bands/82/memberships')) return response({ memberships: [] });
    if (value.includes('/bands/81/invite-links') || value.includes('/bands/82/invite-links')) return response({ invite_links: [] });
    if (value.includes('/bands/81/announcement') || value.includes('/bands/82/announcement')) return response({ announcement: null });
    if (value.includes('/bands/81/profile') && options.method === 'PATCH') return save.promise;
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
    if (value.includes('/bands/43/memberships')) return response({ memberships: [] });
    if (value.includes('/bands/43/invite-links')) return response({ invite_links: [] });
    if (value.includes('/bands/43/announcement') && method === 'GET') return response({ announcement: null });
    if (value.includes('/bands/43/announcement') && method === 'PUT') { const body = JSON.parse(options.body); announcementWrites.push(body); return response({ announcement: { id: 1, content: body.content, visibility: body.visibility, can_manage: true } }); }
    if (value.includes('/bands/43/gallery/featured')) return response({ gallery: null });
    if (value.includes('/bands/43/gallery?') && method === 'GET') {
      const parsed = new URL(value, 'http://localhost'); const offset = Number(parsed.searchParams.get('offset')); galleryOffsets.push(offset); galleryReads += 1;
      const startId = offset === 0 ? 1 : 21;
      return response({ gallery: Array.from({ length: offset === 0 ? 20 : 1 }, (_, index) => ({ id: startId + index, filename: `photo-${startId + index}.jpg`, url: `https://media.invalid/photo-${startId + index}.jpg${offset === 0 && startId + index === 1 && galleryReads > 1 ? '?fresh=1' : ''}`, is_featured: false })), pagination: { total: 21, has_more: offset === 0, next_offset: offset === 0 ? 20 : null } });
    }
    if (value.includes('/bands/43/gallery/1/featured') && method === 'PATCH') { const body = JSON.parse(options.body); featuredWrites.push(body); return response({ gallery: { id: 1, filename: 'photo-1.jpg', url: 'https://media.invalid/photo-1.jpg', is_featured: body.is_featured } }); }
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
    if (value.includes('/bands/44/memberships')) return response({ memberships: [] });
    if (value.includes('/bands/44/invite-links')) return response({ invite_links: [] });
    if (value.includes('/bands/44/announcement')) return response({ announcement: null });
    if (value.includes('/bands/44/gallery/featured') && method === 'GET') { featureReads += 1; return featureReads === 1 ? response({ gallery: { id: 99, filename: 'old.jpg', url: 'https://media.invalid/old.jpg', is_featured: true } }) : lateRefresh.promise; }
    if (value.includes('/bands/44/gallery?limit=20&offset=0')) return response({ gallery: [{ id: 1, filename: 'new.jpg', url: 'https://media.invalid/new.jpg', is_featured: false }], pagination: { total: 1, has_more: false } });
    if (value.includes('/bands/44/gallery/99/featured') && method === 'PATCH') return response({ gallery: { id: 99, filename: 'old.jpg', url: 'https://media.invalid/old.jpg', is_featured: false } });
    throw new Error(`unexpected ${method} ${value}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/featured-race/manage', manage: true }));
    await view.findByText('사진 밴드 관리'); await user.click(view.getByRole('button', { name: '갤러리에서 선택' }));
    await view.findByAltText('현재 대표 사진'); fireEvent.error(view.getByAltText('현재 대표 사진'));
    await waitFor(() => assert.equal(featureReads, 2));
    await user.click(view.getByRole('button', { name: '대표 사진 해제' }));
    await waitFor(() => assert.equal(view.queryByText('현재 대표 사진') === null, true));
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
    if (value.includes('/bands/45/memberships')) return response({ memberships: [] });
    if (value.includes('/bands/45/invite-links')) return response({ invite_links: [{ id: 12, created_at: '2026-01-01T00:30:00Z' }] });
    if (value.includes('/bands/45/announcement')) return response({ announcement: null });
    if (value.includes('/bands/45/gallery/featured')) return response({ error: 'not found' }, 404);
    if (value.includes('/bands/45/gallery?limit=20&offset=0')) return response({ gallery: [{ id: 8, filename: 'available.jpg', url: 'https://media.invalid/available.jpg' }], pagination: { total: 1, has_more: false } });
    if (value.includes('/bands/45/gallery/8/featured') && options.method === 'PATCH') {
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
    if (value.includes('/bands/46/memberships')) return response({ memberships: [] });
    if (value.includes('/bands/46/invite-links')) return response({ invite_links: [] });
    if (value.includes('/bands/46/announcement')) return response({ announcement: null });
    if (value.includes('/bands/46/gallery/featured')) return mode === 'list-404' ? response({ error: 'not found' }, 404) : response({ error: 'unavailable' }, 500);
    if (value.includes('/bands/46/gallery?limit=20&offset=0')) return mode === 'list-404'
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
    if (value.includes('/invite-links')) return response({ invite_links: [] });
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
    assert.equal(view.queryByRole('button', { name: '공지 수정' }), null); assert.equal(view.queryByRole('button', { name: '새 초대 링크 만들기' }), null);
    view.unmount();
  } finally { globalThis.fetch = previous; }
});
