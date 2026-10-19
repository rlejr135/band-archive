import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, location: dom.window.location, history: dom.window.history, sessionStorage: dom.window.sessionStorage, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
let vite; let auth; let invitePage; let inviteService; let bands;
const load = async () => {
  if (invitePage) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
  invitePage = await vite.ssrLoadModule('/src/components/bands/InviteLink.jsx');
  inviteService = await vite.ssrLoadModule('/src/services/inviteLinks.js');
};
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());

const account = (id = null) => ({
  user: id ? { id, email: `${id}@example.test` } : null,
  authGeneration: 1,
  getAccessToken: async () => id ? `token-${id}` : null,
  refreshSession: async () => id ? `token-${id}` : null,
  loading: false,
});
const opaque = (character = 'A') => character.repeat(42) + 'A';
const setUrl = (url) => { dom.reconfigure({ url }); globalThis.location = dom.window.location; globalThis.history = dom.window.history; globalThis.sessionStorage = dom.window.sessionStorage; };
const invitePayload = (viewer = { membership_status: null, can_open_workspace: false }) => ({
  invite: {
    id: 17, expires_at: '2099-01-01T00:00:00Z',
    band: { id: 3, name: '초대 밴드', slug: 'invite-band', description: '소개 내용' },
    featured_gallery: null, stats: { total_songs: 4 }, song_count_scope: 'PUBLIC', announcement: null,
  }, viewer,
});
const tree = (authValue, entry = '/invite') => createElement(auth.AuthContext.Provider, { value: authValue },
  createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null,
    createElement(Route, { path: '/invite', element: createElement(invitePage.InviteLinkRoute) }),
    createElement(Route, { path: '/invite/continue', element: createElement(invitePage.InviteContinueRoute) }),
    createElement(Route, { path: '/bands/:slug', element: createElement('p', null, 'workspace') }))));

test('invite fragment is scrubbed synchronously; preview is body-only and never accepts implicitly', async () => {
  await load(); inviteService.resetInviteUrlContextForTests();
  setUrl(`http://localhost/invite#token=${opaque()}`);
  inviteService.initializeInviteUrlContext();
  assert.equal(dom.window.location.hash, '');
  assert.equal(document.querySelector('meta[name="referrer"]')?.content, 'no-referrer');
  const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => { calls.push({ url: String(url), options }); return response(invitePayload()); };
  try {
    const view = render(tree(account()));
    await view.findByRole('heading', { name: '초대 밴드' });
    assert.equal(calls.length, 1);
    assert.ok(calls[0].url.endsWith('/api/v2/invite-links/preview'));
    assert.equal(calls[0].options.method, 'POST');
    assert.deepEqual(JSON.parse(calls[0].options.body), { token: opaque() });
    assert.equal(calls.some(({ url }) => url.includes(opaque())), false);
    assert.equal(calls.some(({ url }) => url.endsWith('/accept')), false);
    assert.ok(view.getByRole('link', { name: '로그인하고 계속' }));
    assert.ok(view.getByRole('link', { name: '회원가입하고 계속' }));
  } finally { globalThis.fetch = previous; inviteService.resetInviteUrlContextForTests(); }
});

test('signed-in visitor must explicitly supply a member name and accept after resume', async () => {
  await load(); inviteService.resetInviteUrlContextForTests();
  setUrl(`http://localhost/invite#token=${opaque()}`); inviteService.initializeInviteUrlContext();
  const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); calls.push({ path, options });
    if (path.endsWith('/resume-context')) return response({ context: opaque('B'), expires_at: '2099-01-01T00:00:00Z' }, 201);
    if (path.endsWith('/resume')) return response(invitePayload());
    if (path.endsWith('/accept')) return response({ joined: true, already_member: false, band: { id: 3, name: '초대 밴드', slug: 'invite-band' }, membership: { status: 'active', role: 'member' } });
    return response(invitePayload());
  };
  try {
    const view = render(tree(account('visitor')));
    await view.findByRole('heading', { name: '초대 밴드' });
    assert.equal(calls.some(({ path }) => path.endsWith('/accept')), false);
    await userEvent.click(view.getByRole('button', { name: '초대 확인하고 계속' }));
    await view.findByRole('button', { name: '초대받은 밴드에 가입' });
    const acceptButton = view.getByRole('button', { name: '초대받은 밴드에 가입' });
    assert.equal(calls.some(({ path }) => path.endsWith('/accept')), false);
    assert.equal(acceptButton.disabled, true);
    await userEvent.type(view.getByLabelText('밴드에서 사용할 이름 (필수)'), '  초대 이름  ');
    await userEvent.click(acceptButton);
    await waitFor(() => assert.ok(calls.some(({ path }) => path.endsWith('/accept'))));
    const acceptCall = calls.find(({ path }) => path.endsWith('/accept'));
    assert.deepEqual(JSON.parse(acceptCall.options.body), { context: opaque('B'), name: '초대 이름' });
    await view.findByText('workspace');
  } finally { globalThis.fetch = previous; inviteService.resetInviteUrlContextForTests(); }
});

test('verify callback scrubs only invite_resume and preserves Supabase auth params', async () => {
  await load(); inviteService.resetInviteUrlContextForTests();
  setUrl(`http://localhost/verify-email?code=provider-code&invite_resume=${opaque('C')}#provider-fragment`);
  inviteService.initializeInviteUrlContext();
  assert.equal(dom.window.location.search, '?code=provider-code');
  assert.equal(dom.window.location.hash, '#provider-fragment');
  assert.equal(inviteService.getInviteResumeContext(), opaque('C'));
  assert.equal(dom.window.sessionStorage.getItem('band-invite-resume-context').includes(opaque('C')), true);
  assert.equal(dom.window.sessionStorage.getItem('band-invite-resume-context').includes('token'), false);
  inviteService.resetInviteUrlContextForTests();
});

test('invalid private invite returns a generic error and performs no join write', async () => {
  await load(); inviteService.resetInviteUrlContextForTests();
  setUrl(`http://localhost/invite#token=${opaque('D')}`); inviteService.initializeInviteUrlContext();
  const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => { calls.push([String(url), options]); return response({}, 404); };
  try {
    const view = render(tree(account()));
    await view.findByRole('alert');
    assert.equal(calls.length, 1);
    assert.equal(calls[0][1].method, 'POST');
    assert.equal(calls.some(([url]) => url.endsWith('/accept')), false);
  } finally { globalThis.fetch = previous; inviteService.resetInviteUrlContextForTests(); }
});

test('a new SPA fragment supersedes the old preview and its continuation uses only the new invite', async () => {
  await load(); inviteService.resetInviteUrlContextForTests();
  setUrl(`http://localhost/invite#token=${opaque('A')}`); inviteService.initializeInviteUrlContext();
  const previous = globalThis.fetch; const oldPreview = {}; let oldResolve; const calls = [];
  oldPreview.promise = new Promise((resolve) => { oldResolve = resolve; });
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ path, body, options });
    if (path.endsWith('/preview')) return body.token === opaque('A') ? oldPreview.promise : response(invitePayload({ membership_status: null, can_open_workspace: false }));
    if (path.endsWith('/resume-context')) return response({ context: opaque('E'), expires_at: '2099-01-01T00:00:00Z' }, 201);
    if (path.endsWith('/resume')) return response(invitePayload());
    return response({});
  };
  try {
    const view = render(tree(account('visitor')));
    await waitFor(() => assert.equal(calls.filter(({ path }) => path.endsWith('/preview')).length, 1));
    setUrl(`http://localhost/invite#token=${opaque('B')}`);
    view.rerender(tree(account('visitor')));
    await view.findByRole('heading', { name: '초대 밴드' });
    await act(async () => oldResolve(response({ ...invitePayload(), invite: { ...invitePayload().invite, band: { id: 4, name: '오래된 초대', slug: 'old-band', description: '' } } })));
    assert.equal(view.queryByRole('heading', { name: '오래된 초대' }), null);
    await userEvent.click(view.getByRole('button', { name: '초대 확인하고 계속' }));
    await view.findByRole('button', { name: '초대받은 밴드에 가입' });
    const created = calls.find(({ path }) => path.endsWith('/resume-context'));
    assert.deepEqual(created.body, { token: opaque('B'), email: 'visitor@example.test' });
  } finally { globalThis.fetch = previous; inviteService.resetInviteUrlContextForTests(); }
});

test('logout clears an in-flight resume-context creation and its late response cannot restore storage', async () => {
  await load(); inviteService.resetInviteUrlContextForTests();
  setUrl(`http://localhost/invite#token=${opaque('H')}`); inviteService.initializeInviteUrlContext();
  let resolveResponse; const pending = new Promise((resolve) => { resolveResponse = resolve; });
  const previous = globalThis.fetch; globalThis.fetch = async () => pending;
  try {
    const creation = inviteService.createInviteResumeContext('member@example.test');
    inviteService.clearInviteResumeContext();
    inviteService.clearPendingInviteToken();
    resolveResponse(response({ context: opaque('I'), expires_at: '2099-01-01T00:00:00Z' }, 201));
    await assert.rejects(creation, { name: 'AbortError' });
    assert.equal(inviteService.getInviteResumeContext(), '');
    assert.equal(dom.window.sessionStorage.getItem('band-invite-resume-context'), null);
  } finally { globalThis.fetch = previous; inviteService.resetInviteUrlContextForTests(); }
});

test('late accept response cannot navigate after same-account auth generation changes', async () => {
  await load(); inviteService.resetInviteUrlContextForTests(); inviteService.saveInviteResumeContext(opaque('F'), '2099-01-01T00:00:00Z');
  const previous = globalThis.fetch; let acceptResolve; const acceptPromise = new Promise((resolve) => { acceptResolve = resolve; });
  globalThis.fetch = async (url) => String(url).endsWith('/accept') ? acceptPromise : response(invitePayload());
  try {
    const view = render(tree(account('same-user'), '/invite/continue'));
    await view.findByRole('button', { name: '초대받은 밴드에 가입' });
    await userEvent.type(view.getByLabelText('밴드에서 사용할 이름 (필수)'), '이전 이름');
    await userEvent.click(view.getByRole('button', { name: '초대받은 밴드에 가입' }));
    view.rerender(createElement(auth.AuthContext.Provider, { value: { ...account('same-user'), authGeneration: 2 } },
      createElement(MemoryRouter, { initialEntries: ['/invite/continue'] }, createElement(Routes, null,
        createElement(Route, { path: '/invite/continue', element: createElement(invitePage.InviteContinueRoute) }),
        createElement(Route, { path: '/bands/:slug', element: createElement('p', null, 'workspace') })))));
    await act(async () => acceptResolve(response({ joined: true, already_member: false, band: { id: 3, slug: 'late-band' }, membership: { status: 'active' } })));
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(view.queryByText('workspace'), null);
    assert.equal(view.queryByDisplayValue('이전 이름'), null);
  } finally { globalThis.fetch = previous; inviteService.resetInviteUrlContextForTests(); }
});

test('expired and malformed callback contexts are pruned while duplicate markers preserve provider parameters', async () => {
  await load(); inviteService.resetInviteUrlContextForTests();
  setUrl(`http://localhost/verify-email?code=provider-code&invite_resume=${opaque('C')}&invite_resume=${opaque('D')}#provider-fragment`);
  inviteService.initializeInviteUrlContext();
  assert.equal(dom.window.location.search, '?code=provider-code');
  assert.equal(dom.window.location.hash, '#provider-fragment');
  assert.equal(inviteService.getInviteResumeContext(), '');
  dom.window.sessionStorage.setItem('band-invite-resume-context', JSON.stringify({ context: opaque('A'), expires_at: '2000-01-01T00:00:00Z' }));
  assert.equal(inviteService.getInviteResumeContext(), '');
  assert.equal(dom.window.sessionStorage.getItem('band-invite-resume-context'), null);
  dom.window.sessionStorage.setItem('band-invite-resume-context', JSON.stringify({ context: opaque('G'), expires_at: '2099-01-01T00:00:00Z' }));
  inviteService.resetInviteUrlMemoryForTests();
  assert.equal(inviteService.getInviteResumeContext(), opaque('G'));
  assert.equal(inviteService.getInviteResumeContext(), opaque('G'));
  dom.window.sessionStorage.setItem('band-invite-resume-context', JSON.stringify({ context: opaque('G'), expires_at: '2000-01-01T00:00:00Z' }));
  inviteService.resetInviteUrlMemoryForTests();
  assert.equal(inviteService.getInviteResumeContext(), '');
  assert.equal(dom.window.sessionStorage.getItem('band-invite-resume-context'), null);
  inviteService.resetInviteUrlContextForTests();
});

test('invite name is retained on failure and a new context clears drafts and ignores a late accept', async () => {
  await load(); inviteService.resetInviteUrlContextForTests(); inviteService.saveInviteResumeContext(opaque('J'), '2099-01-01T00:00:00Z');
  const previous = globalThis.fetch; const writes = []; let acceptResolve; let acceptCount = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/accept')) { writes.push(JSON.parse(options.body)); acceptCount += 1; return acceptCount === 1 ? response({}, 500) : new Promise((resolve) => { acceptResolve = resolve; }); }
    return response(invitePayload());
  };
  try {
    const view = render(tree(account('member'), '/invite/continue'));
    await view.findByLabelText('밴드에서 사용할 이름 (필수)');
    await userEvent.type(view.getByLabelText('밴드에서 사용할 이름 (필수)'), '내 멤버 이름');
    await userEvent.click(view.getByRole('button', { name: '초대받은 밴드에 가입' })); await view.findByRole('alert');
    assert.equal(view.getByLabelText('밴드에서 사용할 이름 (필수)').value, '내 멤버 이름');
    await userEvent.click(view.getByRole('button', { name: '초대받은 밴드에 가입' }));
    await waitFor(() => assert.equal(writes.length, 2));
    inviteService.saveInviteResumeContext(opaque('K'), '2099-01-01T00:00:00Z'); view.rerender(tree(account('member'), '/invite/continue'));
    await view.findByLabelText('밴드에서 사용할 이름 (필수)');
    assert.equal(view.getByLabelText('밴드에서 사용할 이름 (필수)').value, '');
    assert.equal(view.getByLabelText('밴드에서 사용할 이름 (필수)').disabled, false);
    await act(async () => acceptResolve(response({ joined: true, band: { slug: 'old-band' } })));
    assert.equal(view.queryByText('workspace'), null);
    assert.equal(inviteService.getInviteResumeContext(), opaque('K'));
    await userEvent.type(view.getByLabelText('밴드에서 사용할 이름 (필수)'), '새 밴드 이름');
    assert.equal(view.getByRole('button', { name: '초대받은 밴드에 가입' }).disabled, false);
  } finally { globalThis.fetch = previous; inviteService.resetInviteUrlContextForTests(); }
});

test('active invite membership opens the workspace without asking for a name or submitting', async () => {
  await load(); inviteService.resetInviteUrlContextForTests(); inviteService.saveInviteResumeContext(opaque('L'), '2099-01-01T00:00:00Z');
  const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response(invitePayload({ membership_status: 'active', can_open_workspace: true })); };
  try { const view = render(tree(account('member'), '/invite/continue')); await view.findByRole('link', { name: '밴드 공간 열기' }); assert.equal(view.queryByLabelText('밴드에서 사용할 이름 (필수)'), null); assert.equal(calls.some((url) => url.endsWith('/accept')), false); }
  finally { globalThis.fetch = previous; inviteService.resetInviteUrlContextForTests(); }
});

test('successful invite refreshes the band switcher but a failed list refresh does not undo the join', async () => {
  await load(); inviteService.resetInviteUrlContextForTests(); inviteService.saveInviteResumeContext(opaque('M'), '2099-01-01T00:00:00Z');
  const previous = globalThis.fetch; const reloads = [];
  globalThis.fetch = async (url) => String(url).endsWith('/accept') ? response({ joined: true, band: { slug: 'joined-band' } }) : response(invitePayload());
  try {
    const view = render(createElement(bands.BandContext.Provider, { value: { reload: (options) => { reloads.push(options); return Promise.reject(new Error('offline membership refresh')); } } }, tree(account('member'), '/invite/continue')));
    await view.findByLabelText('밴드에서 사용할 이름 (필수)'); await userEvent.type(view.getByLabelText('밴드에서 사용할 이름 (필수)'), '멤버');
    await userEvent.click(view.getByRole('button', { name: '초대받은 밴드에 가입' })); await view.findByText('workspace');
    assert.deepEqual(reloads, [{ preserveSelection: true }]); assert.equal(view.queryByRole('alert'), null);
  } finally { globalThis.fetch = previous; inviteService.resetInviteUrlContextForTests(); }
});
