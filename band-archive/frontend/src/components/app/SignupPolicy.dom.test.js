import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { createElement, StrictMode, useEffect } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';
import userEvent from '@testing-library/user-event';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
let vite; let pages; let policy; let auth; let gate; let challenge; let policyPages; let inviteService; let requireAuth; let inviteRoute;
const load = async () => {
  if (pages) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true }, plugins: [{ name: 'auth-test-double', enforce: 'pre', transform(_code, id) {
    if (id.replaceAll('\\', '/').endsWith('/src/services/supabase.js')) return 'export const getSupabaseClient = () => globalThis.testSupabase; export const getSupabaseConfigError = () => null;';
  } }] });
  pages = await vite.ssrLoadModule('/src/components/auth/AuthPages.jsx');
  policy = await vite.ssrLoadModule('/src/context/ServicePolicyContext.jsx');
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  gate = await vite.ssrLoadModule('/src/components/auth/PolicyAcceptanceGate.jsx');
  challenge = await vite.ssrLoadModule('/src/components/auth/TurnstileChallenge.jsx');
  policyPages = await vite.ssrLoadModule('/src/components/auth/PolicyPages.jsx');
  inviteService = await vite.ssrLoadModule('/src/services/inviteLinks.js');
  requireAuth = await vite.ssrLoadModule('/src/components/layout/RequireAuth.jsx');
  inviteRoute = await vite.ssrLoadModule('/src/components/bands/InviteLink.jsx');
};
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => { cleanup(); delete window.turnstile; delete globalThis.testSupabase; });
const config = (extra = {}) => ({ service: { name: 'Test archive', operator_name: 'Tester', contact_email: 'contact@example.test', sender_email: 'sender@example.test' }, captcha: { enabled: false, site_key: null }, policies: { ready: true, policy_acceptance_enabled: true, terms_version: 't1', privacy_version: 'p1', retention_notice: 'Confirmed retention', providers_notice: 'Confirmed providers' }, ...extra });
const context = (value) => ({ config: value, loading: false, error: '', retry: () => {} });
const account = (id = null) => ({ user: id ? { id } : null, loading: false, configError: null, getAccessToken: async () => id, refreshSession: async () => id, signOut: async () => {} });
const tree = (Component, value = config(), user = account(), props = {}) => createElement(policy.ServicePolicyContext.Provider, { value: context(value) }, createElement(auth.AuthContext.Provider, { value: user }, createElement(MemoryRouter, null, createElement(Component, props))));
const fill = async (view) => { await userEvent.type(view.getByLabelText('이메일'), 'person@example.test'); await userEvent.type(view.getByLabelText('비밀번호'), 'a-long-password'); };
const response = (body) => ({ ok: true, json: async () => body });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

test('signup requires separate terms and age confirmations when rollout enabled and passes no policy metadata', async () => {
  await load(); const writes = []; globalThis.testSupabase = { auth: { signUp: async (payload) => { writes.push(payload); return {}; } } };
  const view = render(tree(pages.SignupPage)); await fill(view);
  const button = view.getByRole('button', { name: '회원가입' }); assert.equal(button.disabled, true);
  fireEvent.click(view.getByRole('checkbox', { name: /이용약관/ })); assert.equal(button.disabled, true);
  fireEvent.click(view.getByRole('checkbox', { name: /만 14세/ })); fireEvent.click(button);
  await view.findByRole('status'); assert.equal(writes.length, 1); assert.equal(writes[0].options.data, undefined); assert.equal(writes[0].options.emailRedirectTo, 'http://localhost/verify-email');
});

test('invite signup uses only an opaque callback context and sends the raw token only in the resume-context body', async () => {
  await load();
  const previousFetch = globalThis.fetch; const rawToken = 'A'.repeat(43); const resumeContext = 'E'.repeat(42) + 'A'; const writes = [];
  dom.reconfigure({ url: `http://localhost/invite#token=${rawToken}` });
  globalThis.location = dom.window.location; globalThis.history = dom.window.history; globalThis.sessionStorage = dom.window.sessionStorage;
  inviteService.resetInviteUrlContextForTests(); inviteService.initializeInviteUrlContext();
  globalThis.fetch = async (url, options = {}) => { writes.push({ url: String(url), options }); return response({ context: resumeContext, expires_at: '2099-01-01T00:00:00Z' }); };
  globalThis.testSupabase = { auth: { signUp: async (payload) => { writes.push({ signup: payload }); return {}; } } };
  const view = render(createElement(policy.ServicePolicyContext.Provider, { value: context(config()) },
    createElement(auth.AuthContext.Provider, { value: account() },
      createElement(MemoryRouter, { initialEntries: ['/signup?returnTo=%2Finvite%2Fcontinue'] }, createElement(pages.SignupPage)))));
  try {
    await fill(view);
    fireEvent.click(view.getByRole('checkbox', { name: /이용약관/ })); fireEvent.click(view.getByRole('checkbox', { name: /만 14세/ }));
    fireEvent.click(view.getByRole('button', { name: '회원가입' }));
    await view.findByRole('status');
    const contextCall = writes.find((item) => item.url?.endsWith('/api/v2/invite-links/resume-context'));
    assert.ok(contextCall);
    assert.deepEqual(JSON.parse(contextCall.options.body), { token: rawToken, email: 'person@example.test' });
    const signup = writes.find((item) => item.signup)?.signup;
    assert.equal(signup.options.emailRedirectTo, `http://localhost/verify-email?invite_resume=${resumeContext}`);
    assert.equal(signup.options.emailRedirectTo.includes(rawToken), false);
    assert.equal(signup.options.data, undefined);
    view.unmount();
    dom.reconfigure({ url: `http://localhost/verify-email?code=provider-code&invite_resume=${resumeContext}` });
    globalThis.location = dom.window.location; globalThis.history = dom.window.history; globalThis.sessionStorage = dom.window.sessionStorage;
    inviteService.initializeInviteUrlContext();
    assert.equal(dom.window.location.search, '?code=provider-code');
    const verified = render(createElement(auth.AuthContext.Provider, { value: account('verified') }, createElement(MemoryRouter, null, createElement(pages.VerifyEmailPage))));
    assert.ok(verified.getByRole('link', { name: '초대받은 밴드 확인하기' }));
    verified.unmount();

    // Simulate a fresh browser/module context on the email callback: the only
    // continuation data is the opaque callback marker, never the raw token.
    inviteService.resetInviteUrlContextForTests(); dom.window.sessionStorage.clear();
    dom.reconfigure({ url: `http://localhost/verify-email?code=provider-code&invite_resume=${resumeContext}` });
    globalThis.location = dom.window.location; globalThis.history = dom.window.history; globalThis.sessionStorage = dom.window.sessionStorage;
    inviteService.initializeInviteUrlContext();
    assert.equal(inviteService.getInviteResumeContext(), resumeContext);
    const previousContinuationFetch = globalThis.fetch; const continuationWrites = [];
    globalThis.fetch = async (url, options = {}) => {
      const path = String(url); continuationWrites.push({ path, options });
      if (path.endsWith('/api/v2/me')) return response({ user: { policy_acceptance_required: true } });
      if (path.endsWith('/policy-acceptance')) return response({ accepted: true });
      if (path.endsWith('/invite-links/resume')) return response({
        invite: { id: 17, expires_at: '2099-01-01T00:00:00Z', band: { id: 3, name: '초대 밴드', slug: 'invite-band', description: '소개' }, featured_gallery: null, stats: { total_songs: 4 }, song_count_scope: 'PUBLIC', announcement: null },
        viewer: { membership_status: 'pending', can_open_workspace: false },
      });
      if (path.endsWith('/invite-links/accept')) return response({ joined: true, already_member: false, band: { id: 3, name: '초대 밴드', slug: 'invite-band' }, membership: { status: 'active', role: 'member' } });
      throw new Error(`Unexpected continuation request: ${path}`);
    };
    const protectedFlow = render(createElement(policy.ServicePolicyContext.Provider, { value: context(config()) },
      createElement(auth.AuthContext.Provider, { value: account('verified') }, createElement(MemoryRouter, { initialEntries: ['/invite/continue'] },
        createElement(requireAuth.default, null, createElement(inviteRoute.InviteContinueRoute))))));
    await protectedFlow.findByRole('heading', { name: '가입 확인' });
    assert.equal(protectedFlow.queryByRole('button', { name: '초대받은 밴드에 가입' }), null);
    assert.equal(continuationWrites.some(({ path }) => path.endsWith('/invite-links/accept')), false);
    fireEvent.click(protectedFlow.getByRole('checkbox', { name: /이용약관/ }));
    fireEvent.click(protectedFlow.getByRole('checkbox', { name: /만 14세/ }));
    fireEvent.click(protectedFlow.getByRole('button', { name: '확인하고 계속하기' }));
    await protectedFlow.findByRole('heading', { name: '초대 밴드 밴드에 가입하시겠습니까?' });
    assert.equal(continuationWrites.some(({ path }) => path.endsWith('/invite-links/accept')), false);
    await userEvent.type(protectedFlow.getByLabelText('밴드에서 사용할 이름 (필수)'), '가입 멤버');
    await userEvent.click(protectedFlow.getByRole('button', { name: '초대받은 밴드에 가입' }));
    await waitFor(() => assert.ok(continuationWrites.some(({ path }) => path.endsWith('/invite-links/accept'))));
    const acceptRequest = continuationWrites.find(({ path }) => path.endsWith('/invite-links/accept'));
    assert.deepEqual(JSON.parse(acceptRequest.options.body), { context: resumeContext, name: '가입 멤버' });
    globalThis.fetch = previousContinuationFetch;
  } finally { globalThis.fetch = previousFetch; inviteService.resetInviteUrlContextForTests(); delete globalThis.testSupabase; }
});

test('email verification preserves the fixed invite return only while an opaque invite context exists', async () => {
  await load(); inviteService.resetInviteUrlContextForTests();
  const context = 'M'.repeat(42) + 'A';
  inviteService.saveInviteResumeContext(context, '2099-01-01T00:00:00Z');
  try {
    const invited = render(tree(pages.VerifyEmailPage));
    assert.match(invited.getByRole('status').textContent, /이메일 인증을 완료했다면 로그인해 초대를 이어가세요/);
    assert.equal(invited.getByRole('link', { name: '로그인 후 초대 이어가기' }).getAttribute('href'), '/login?returnTo=%2Finvite%2Fcontinue');
    assert.equal(invited.queryByRole('link', { name: '밴드 선택' }), null);
    invited.unmount();

    inviteService.clearInviteResumeContext();
    const ordinary = render(tree(pages.VerifyEmailPage));
    assert.equal(ordinary.getByRole('link', { name: '로그인' }).getAttribute('href'), '/login');
    const accountLink = ordinary.getByRole('link', { name: '계정과 밴드' });
    assert.equal(accountLink.getAttribute('href'), '/account');
  } finally { inviteService.resetInviteUrlContextForTests(); }
});

test('login and signup abort pending invite-context creation on unmount before calling Supabase', async () => {
  await load(); const previousFetch = globalThis.fetch; const rawToken = 'J'.repeat(42) + 'A';
  try {
    for (const [Component, route, label] of [[pages.LoginPage, 'login', '로그인'], [pages.SignupPage, 'signup', '회원가입']]) {
      inviteService.resetInviteUrlContextForTests();
      dom.reconfigure({ url: `http://localhost/invite#token=${rawToken}` });
      globalThis.location = dom.window.location; globalThis.history = dom.window.history; globalThis.sessionStorage = dom.window.sessionStorage;
      inviteService.initializeInviteUrlContext();
      let requestOptions; let resolveResponse; const pending = new Promise((resolve) => { resolveResponse = resolve; }); const supabaseCalls = [];
      globalThis.fetch = async (_url, options) => { requestOptions = options; return pending; };
      globalThis.testSupabase = { auth: {
        signInWithPassword: async () => { supabaseCalls.push('login'); return {}; },
        signUp: async () => { supabaseCalls.push('signup'); return {}; },
      } };
      const returnTo = encodeURIComponent('/invite/continue');
      const view = render(createElement(policy.ServicePolicyContext.Provider, { value: context(config()) },
        createElement(auth.AuthContext.Provider, { value: account() }, createElement(MemoryRouter, { initialEntries: [`/${route}?returnTo=${returnTo}`] }, createElement(Component)))));
      await fill(view);
      if (route === 'signup') {
        fireEvent.click(view.getByRole('checkbox', { name: /이용약관/ }));
        fireEvent.click(view.getByRole('checkbox', { name: /만 14세/ }));
      }
      fireEvent.click(view.getByRole('button', { name: label }));
      await waitFor(() => assert.ok(requestOptions));
      view.unmount();
      assert.equal(requestOptions.signal.aborted, true);
      await act(async () => { resolveResponse(response({ context: 'K'.repeat(42) + 'A', expires_at: '2099-01-01T00:00:00Z' }, 201)); await pending; });
      await new Promise((resolve) => setTimeout(resolve, 0));
      assert.deepEqual(supabaseCalls, []);
      assert.equal(inviteService.getInviteResumeContext(), '');
    }
  } finally { globalThis.fetch = previousFetch; inviteService.resetInviteUrlContextForTests(); delete globalThis.testSupabase; }
});

test('rollout off and incomplete draft preserve legacy signup without false consent', async () => {
  await load(); let writes = 0; globalThis.testSupabase = { auth: { signUp: async () => { writes += 1; return {}; } } };
  const view = render(tree(pages.SignupPage, config({ policies: { ready: false, policy_acceptance_enabled: false } }))); await fill(view);
  assert.equal(view.queryByRole('checkbox'), null); assert.equal(view.getByRole('button', { name: '회원가입' }).disabled, false);
  fireEvent.click(view.getByRole('button', { name: '회원가입' })); await waitFor(() => assert.equal(writes, 1));
});

test('required CAPTCHA blocks missing/expired token, passes valid token, removes widget after attempt', async () => {
  await load(); let options; const removed = []; const writes = [];
  window.turnstile = { render: (_element, supplied) => { options = supplied; return 'widget'; }, remove: (id) => removed.push(id) };
  globalThis.testSupabase = { auth: { signInWithPassword: async (payload) => { writes.push(payload); return { error: { message: 'generic' } }; } } };
  const view = render(tree(pages.LoginPage, config({ captcha: { enabled: true, site_key: 'public-test-key' } }))); await fill(view);
  const button = view.getByRole('button', { name: '로그인' }); assert.equal(button.disabled, true);
  await waitFor(() => assert.ok(options)); act(() => options.callback('first')); assert.equal(button.disabled, false);
  act(() => options['expired-callback']()); assert.equal(button.disabled, true); assert.equal(writes.length, 0);
  act(() => options.callback('valid-token')); fireEvent.click(button); await view.findByText('요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.');
  assert.equal(writes[0].options.captchaToken, 'valid-token'); assert.equal(button.disabled, true); assert.ok(removed.includes('widget'));
});

test('forgot password attaches captchaToken and keeps account-existence response generic', async () => {
  await load(); let options; let sent;
  window.turnstile = { render: (_element, supplied) => { options = supplied; return 'pw'; }, remove: () => {} };
  globalThis.testSupabase = { auth: { resetPasswordForEmail: async (...args) => { sent = args; return { error: {} }; } } };
  const view = render(tree(pages.ForgotPasswordPage, config({ captcha: { enabled: true, site_key: 'public-test-key' } })));
  await userEvent.type(view.getByLabelText('이메일'), 'person@example.test');
  await waitFor(() => assert.ok(options)); act(() => options.callback('reset-token')); fireEvent.click(view.getByRole('button', { name: '재설정 메일 보내기' }));
  await view.findByText('해당 주소에 계정이 있다면 재설정 메일을 보냈습니다.'); assert.equal(sent[1].captchaToken, 'reset-token');
});

test('widget ignores stale callbacks after strict-mode cleanup and unmount', async () => {
  await load(); const renders = []; const tokens = []; const removed = [];
  window.turnstile = { render: (_element, options) => { renders.push(options); return renders.length; }, remove: (id) => removed.push(id) };
  const view = render(createElement(StrictMode, null, createElement(challenge.TurnstileChallenge, { siteKey: 'key', onToken: (token) => tokens.push(token) })));
  await waitFor(() => assert.equal(renders.length, 1)); view.unmount(); const before = tokens.length;
  renders[0].callback('stale'); renders[0]['error-callback'](); renders[0]['expired-callback'](); assert.equal(tokens.length, before); assert.deepEqual(removed, [1]);
});

test('authenticated new-account policy gate does not mount workspace before receipt', async () => {
  await load(); const previous = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => { if (String(url).endsWith('/policy-acceptance')) { writes.push(JSON.parse(options.body)); return response({ accepted: true }); } return response({ user: { policy_acceptance_required: true } }); };
  try {
    const view = render(tree(gate.PolicyAcceptanceGate, config(), account('new'), { children: createElement('p', null, 'private workspace') }));
    await view.findByRole('heading', { name: '가입 확인' }); await view.findByRole('checkbox', { name: /만 14세/ }); assert.equal(view.queryByText('private workspace'), null);
    fireEvent.click(view.getByRole('checkbox', { name: /이용약관/ })); fireEvent.click(view.getByRole('checkbox', { name: /만 14세/ })); fireEvent.click(view.getByRole('button', { name: '확인하고 계속하기' }));
    await view.findByText('private workspace'); assert.deepEqual(writes, [{ terms_version: 't1', privacy_version: 'p1', age_14_plus: true, terms_accepted: true }]);
  } finally { globalThis.fetch = previous; }
});

test('receipt account switch aborts A request before a 401 can refresh/retry as B', async () => {
  await load(); const previous = globalThis.fetch; const aWrite = deferred(); const writes = []; let token = 'a'; let refreshes = 0;
  const dynamic = (id, generation) => ({ ...account(id), authGeneration: generation, getAccessToken: async () => token, refreshSession: async () => { refreshes += 1; return token; } });
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/policy-acceptance')) { writes.push(options); return aWrite.promise; }
    return response({ user: { policy_acceptance_required: true } });
  };
  try {
    const view = render(tree(gate.PolicyAcceptanceGate, config(), dynamic('a', 1), { children: createElement('p', null, 'workspace') }));
    await view.findByRole('checkbox', { name: /만 14세/ }); fireEvent.click(view.getByRole('checkbox', { name: /만 14세/ })); fireEvent.click(view.getByRole('checkbox', { name: /이용약관/ }));
    const button = view.getByRole('button', { name: '확인하고 계속하기' }); fireEvent.click(button); fireEvent.submit(button.closest('form'));
    await waitFor(() => assert.equal(writes.length, 1)); token = 'b';
    view.rerender(tree(gate.PolicyAcceptanceGate, config(), dynamic('b', 2), { children: createElement('p', null, 'workspace') }));
    assert.equal(writes[0].signal.aborted, true);
    await act(async () => aWrite.resolve({ ok: false, status: 401, json: async () => ({}) }));
    await view.findByRole('checkbox', { name: /만 14세/ }); assert.equal(view.getByRole('checkbox', { name: /만 14세/ }).checked, false);
    assert.equal(view.queryByText('workspace'), null); assert.equal(refreshes, 0); assert.equal(writes.length, 1);
  } finally { globalThis.fetch = previous; }
});

test('policy gate never opens workspace on malformed status or false receipt', async () => {
  await load(); const previous = globalThis.fetch;
  try {
    globalThis.fetch = async () => response({ user: {} });
    const bad = render(tree(gate.PolicyAcceptanceGate, config(), account('new'), { children: createElement('p', null, 'workspace') }));
    await bad.findByRole('alert'); assert.equal(bad.queryByText('workspace'), null); bad.unmount();
    globalThis.fetch = async (url) => response(String(url).endsWith('/policy-acceptance') ? { accepted: false } : { user: { policy_acceptance_required: true } });
    const denied = render(tree(gate.PolicyAcceptanceGate, config(), account('new'), { children: createElement('p', null, 'workspace') }));
    await denied.findByRole('checkbox', { name: /만 14세/ }); fireEvent.click(denied.getByRole('checkbox', { name: /만 14세/ })); fireEvent.click(denied.getByRole('checkbox', { name: /이용약관/ }));
    fireEvent.click(denied.getByRole('button', { name: '확인하고 계속하기' })); await denied.findByRole('alert'); assert.equal(denied.queryByText('workspace'), null);
  } finally { globalThis.fetch = previous; }
});

test('token refresh and repeated same-user SIGNED_IN preserve workspace; logout/relogin changes epoch', async () => {
  await load(); const previous = globalThis.fetch; let authEvent; let mounts = 0; let reads = 0;
  const session = (token) => ({ user: { id: 'a' }, access_token: token });
  globalThis.testSupabase = { auth: { getSession: async () => ({ data: { session: session('one') } }), onAuthStateChange: (callback) => { authEvent = callback; return { data: { subscription: { unsubscribe() {} } } }; } } };
  globalThis.fetch = async () => { reads += 1; return response({ user: { policy_acceptance_required: false } }); };
  const Workspace = () => { useEffect(() => { mounts += 1; }, []); return createElement('p', null, 'player'); };
  const Probe = () => { const value = auth.useAuth(); return createElement('output', { 'data-testid': 'auth-epoch' }, `${value.user?.id || 'none'}:${value.authGeneration}`); };
  try {
    const view = render(createElement(policy.ServicePolicyContext.Provider, { value: context(config()) }, createElement(auth.AuthProvider, null, createElement(MemoryRouter, null, createElement(Probe), createElement(gate.PolicyAcceptanceGate, null, createElement(Workspace))))));
    await view.findByText('player'); await waitFor(() => assert.equal(reads, 1)); const initialMounts = mounts;
    act(() => authEvent('TOKEN_REFRESHED', session('two'))); act(() => authEvent('SIGNED_IN', session('two')));
    assert.equal(view.getByTestId('auth-epoch').textContent, 'a:0'); assert.equal(mounts, initialMounts); assert.equal(reads, 1);
    act(() => authEvent('SIGNED_OUT', null)); assert.equal(view.getByTestId('auth-epoch').textContent, 'none:1');
    act(() => authEvent('SIGNED_IN', session('three'))); await waitFor(() => assert.equal(view.getByTestId('auth-epoch').textContent, 'a:2'));
  } finally { globalThis.fetch = previous; }
});

test('policy version changes clear signup checks and reset pending receipt checks', async () => {
  await load(); const previous = globalThis.fetch;
  const first = config(); const next = config(); next.policies.terms_version = 't2';
  try {
    const signup = render(tree(pages.SignupPage, first));
    fireEvent.click(signup.getByRole('checkbox', { name: /만 14세/ })); fireEvent.click(signup.getByRole('checkbox', { name: /이용약관/ }));
    signup.rerender(tree(pages.SignupPage, next)); assert.equal(signup.getByRole('checkbox', { name: /만 14세/ }).checked, false); assert.equal(signup.getByRole('checkbox', { name: /이용약관/ }).checked, false); signup.unmount();
    globalThis.fetch = async () => response({ user: { policy_acceptance_required: true } });
    const receipt = render(tree(gate.PolicyAcceptanceGate, first, account('new'))); await receipt.findByRole('checkbox', { name: /만 14세/ });
    fireEvent.click(receipt.getByRole('checkbox', { name: /만 14세/ })); fireEvent.click(receipt.getByRole('checkbox', { name: /이용약관/ }));
    receipt.rerender(tree(gate.PolicyAcceptanceGate, next, account('new'))); await receipt.findByRole('checkbox', { name: /만 14세/ });
    assert.equal(receipt.getByRole('checkbox', { name: /만 14세/ }).checked, false); assert.equal(receipt.getByRole('checkbox', { name: /이용약관/ }).checked, false);
  } finally { globalThis.fetch = previous; }
});

test('public policy hides unapproved draft facts and uses runtime contact after readiness', async () => {
  await load(); const draft = config(); draft.policies.ready = false; draft.policies.policy_acceptance_enabled = false;
  const view = render(tree(policyPages.PolicyPage, draft, account(), { kind: 'privacy' }));
  assert.ok(view.getByText(/정식 정책을 준비/)); assert.equal(view.queryByText('Confirmed retention'), null); assert.ok(view.getByRole('link', { name: 'contact@example.test' }));
  const approved = config(); approved.service.contact_email = 'new-contact@example.test';
  view.rerender(tree(policyPages.PolicyPage, approved, account(), { kind: 'privacy' }));
  assert.ok(view.getByText('Confirmed retention'));
  const contactLinks = view.getAllByRole('link', { name: 'new-contact@example.test' });
  assert.equal(contactLinks.length, 2);
  assert.equal(contactLinks.every((link) => link.getAttribute('href') === 'mailto:new-contact@example.test'), true);
  assert.equal(view.queryByText('contact@example.test') === null, true);
});

test('runtime policy timeout is bounded, explicit retry recovers, and old response is ignored', async () => {
  await load(); const previous = globalThis.fetch; const oldTimer = window.setTimeout; const stalled = deferred(); let reads = 0;
  const Probe = () => { const value = policy.useServicePolicy(); return createElement('section', null, createElement('output', null, value.loading ? 'loading policy' : value.error || value.config.service.name), createElement('button', { onClick: value.retry }, 'retry policy')); };
  globalThis.fetch = async () => { reads += 1; return reads === 1 ? stalled.promise : response(config()); };
  window.setTimeout = (callback, delay, ...args) => oldTimer.call(window, callback, delay === 10000 ? 20 : delay, ...args);
  try {
    const view = render(createElement(policy.ServicePolicyProvider, null, createElement(Probe)));
    await view.findByText('서비스 안내 설정 요청 시간이 초과되었습니다.'); fireEvent.click(view.getByRole('button', { name: 'retry policy' }));
    await view.findByText('Test archive'); const oldConfig = config(); oldConfig.service.name = 'stale service';
    await act(async () => stalled.resolve(response(oldConfig))); assert.equal(view.queryByText('stale service'), null); assert.ok(view.getByText('Test archive'));
  } finally { globalThis.fetch = previous; window.setTimeout = oldTimer; }
});
