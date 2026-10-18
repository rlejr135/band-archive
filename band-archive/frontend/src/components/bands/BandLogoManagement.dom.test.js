import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement, Fragment } from 'react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, FormData: dom.window.FormData });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
let vite; let auth; let component; let guard;
const load = async () => {
  if (component) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  component = await vite.ssrLoadModule('/src/components/bands/BandLogoManagement.jsx');
  guard = (await vite.ssrLoadModule('/src/components/layout/RequireAuth.jsx')).default;
};
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());
const account = (id = 'owner') => ({ user: id ? { id } : null, getAccessToken: async () => id ? `token-${id}` : null, refreshSession: async () => null, loading: false });
const payload = ({ slug = 'private-band', name = '비공개 밴드', logoId = null, logoUrl = null, gallery = [{ id: 21, url: 'https://media.example.test/photo-21.jpg', is_featured: true }, { id: 22, url: 'https://media.example.test/photo-22.jpg', is_featured: false }], offset = 0, hasMore = false } = {}) => ({
  band: { id: 81, name, slug }, permissions: { can_manage_logo: true },
  logo: logoId == null ? null : { gallery_image_id: logoId, url: logoUrl },
  gallery, pagination: { limit: 20, offset, total: gallery.length, has_more: hasMore },
});
const renderRoute = (authValue, entry = '/bands/private-band/manage/logo', protectedRoute = false) => {
  const routeElement = protectedRoute ? createElement(guard, null, createElement(component.BandLogoManagementRoute)) : createElement(component.BandLogoManagementRoute);
  const routes = createElement(Routes, null,
    createElement(Route, { path: '/bands/:bandSlug/manage/logo', element: routeElement }),
    createElement(Route, { path: '/login', element: createElement('p', null, '로그인 화면') }));
  return render(createElement(auth.AuthContext.Provider, { value: authValue }, createElement(MemoryRouter, { initialEntries: [entry] }, routes)));
};
const SwitchBand = () => { const navigate = useNavigate(); return createElement('button', { type: 'button', onClick: () => navigate('/bands/second-band/manage/logo') }, '다른 밴드'); };

test('narrow logo page uses only logo-management GET and supports clear when the logo URL is unavailable', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ path: new URL(String(url)).pathname, search: new URL(String(url)).search, method: options.method || 'GET', body: options.body });
    if ((options.method || 'GET') === 'PUT') return response({ logo: null });
    return response(payload({ logoId: 21, logoUrl: null }));
  };
  try {
    const view = renderRoute(account('owner'));
    await view.findByRole('heading', { name: '비공개 밴드 로고 관리' });
    assert.ok(view.getByRole('img', { name: '비공개 밴드 로고를 표시할 수 없음' }));
    assert.ok(view.getByRole('button', { name: '로고 해제' }));
    await userEvent.click(view.getByRole('button', { name: '로고 해제' }));
    await view.findByText('설정된 로고가 없습니다.');
    assert.deepEqual(calls.map(({ path, search, method }) => ({ path, search, method })), [
      { path: '/api/v2/bands/by-slug/private-band/logo-management', search: '?limit=20&offset=0', method: 'GET' },
      { path: '/api/v2/bands/81/logo', search: '', method: 'PUT' },
    ]);
    assert.deepEqual(JSON.parse(calls[1].body), { gallery_image_id: null });
    assert.equal(calls.some(({ path }) => path.endsWith('/memberships') || path.endsWith('/invite-codes') || path === '/api/v2/bands/by-slug/private-band'), false);
  } finally { globalThis.fetch = previous; }
});

test('active platform administrator can set one same-band gallery image without loading workspace data', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const parsed = new URL(String(url)); calls.push({ path: parsed.pathname, method: options.method || 'GET', body: options.body });
    if ((options.method || 'GET') === 'PUT') return response({ logo: { gallery_image_id: 22, url: null } });
    return response(payload());
  };
  try {
    const view = renderRoute(account('platform-admin'));
    await view.findByRole('heading', { name: '비공개 밴드 로고 관리' });
    await userEvent.click(view.getAllByRole('button', { name: '로고로 선택' })[1]);
    await view.findByText('갤러리 사진 #22');
    assert.deepEqual(JSON.parse(calls.find((call) => call.method === 'PUT').body), { gallery_image_id: 22 });
    assert.equal(calls.some((call) => call.path.includes('/memberships') || call.path.includes('/invite-codes') || call.path === '/api/v2/bands/by-slug/private-band'), false);
    assert.equal(view.queryByRole('navigation', { name: '밴드 공간' }), null);
  } finally { globalThis.fetch = previous; }
});

test('candidate pagination requests only the next bounded page and appends its images', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => {
    const parsed = new URL(String(url)); calls.push(parsed.search);
    if (parsed.searchParams.get('offset') === '20') {
      return response(payload({ gallery: [{ id: 41, url: 'https://media.example.test/photo-41.jpg' }], offset: 20, hasMore: false }));
    }
    return response(payload({ gallery: [{ id: 21, url: 'https://media.example.test/photo-21.jpg' }], offset: 0, hasMore: true }));
  };
  try {
    const view = renderRoute(account('owner'));
    await view.findByRole('button', { name: '사진 더 불러오기' });
    await userEvent.click(view.getByRole('button', { name: '사진 더 불러오기' }));
    await view.findAllByRole('button', { name: '로고로 선택' });
    await waitFor(() => assert.equal(view.getAllByRole('button', { name: '로고로 선택' }).length, 2));
    assert.deepEqual(calls, ['?limit=20&offset=0', '?limit=20&offset=20']);
  } finally { globalThis.fetch = previous; }
});

test('gallery logo selection is keyboard operable', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (_url, options = {}) => {
    calls.push({ method: options.method || 'GET', body: options.body });
    return (options.method || 'GET') === 'PUT'
      ? response({ logo: { gallery_image_id: 21, url: null } })
      : response(payload());
  };
  try {
    const user = userEvent.setup({ document: dom.window.document });
    const view = renderRoute(account('owner'));
    await view.findByRole('heading', { name: '비공개 밴드 로고 관리' });
    const select = view.getAllByRole('button', { name: '로고로 선택' })[0];
    select.focus();
    await user.keyboard('{Enter}');
    await view.findByText('갤러리 사진 #21');
    assert.deepEqual(JSON.parse(calls.find((call) => call.method === 'PUT').body), { gallery_image_id: 21 });
  } finally { globalThis.fetch = previous; }
});

test('image refresh during load-more does not strand pagination and refreshes the candidate original page', async () => {
  await load(); const previous = globalThis.fetch; const nextPage = deferred(); const offsets = [];
  globalThis.fetch = async (url, options = {}) => {
    const parsed = new URL(String(url)); const offset = Number(parsed.searchParams.get('offset'));
    offsets.push(offset);
    if (options.method === 'PUT') return response({ logo: { gallery_image_id: 22, url: null } });
    if (offset === 20) return nextPage.promise;
    if (offset === 0 && offsets.length > 1) return response(payload({ gallery: [{ id: 21, url: 'https://media.example.test/refreshed-21.jpg' }], hasMore: true }));
    return response(payload({ gallery: [{ id: 21, url: 'https://media.example.test/photo-21.jpg' }], hasMore: true }));
  };
  try {
    const view = renderRoute(account('owner'));
    const more = await view.findByRole('button', { name: '사진 더 불러오기' });
    await userEvent.click(more);
    await waitFor(() => assert.ok(offsets.includes(20)));
    const image = view.getByRole('img', { name: '갤러리 사진 #21' });
    await act(async () => image.dispatchEvent(new dom.window.Event('error')));
    await waitFor(() => assert.ok(offsets.filter((offset) => offset === 0).length >= 2));
    await act(async () => { nextPage.resolve(response(payload({ gallery: [{ id: 41, url: 'https://media.example.test/photo-41.jpg' }], offset: 20, hasMore: false }))); });
    await view.findByRole('img', { name: '갤러리 사진 #41' });
    assert.equal(view.queryByRole('button', { name: '사진 더 불러오기' }), null);
    assert.equal(view.getByRole('img', { name: '갤러리 사진 #21' }).getAttribute('src'), 'https://media.example.test/refreshed-21.jpg');
    assert.deepEqual(offsets.slice(0, 3), [0, 20, 0]);
  } finally { globalThis.fetch = previous; }
});

test('simultaneous failed images refresh independently without invalidating each other', async () => {
  await load(); const previous = globalThis.fetch; const offsets = [];
  globalThis.fetch = async (url) => {
    const parsed = new URL(String(url)); offsets.push(Number(parsed.searchParams.get('offset')));
    return response(payload({ logoId: 99, logoUrl: 'https://media.example.test/logo-99.jpg', gallery: [{ id: 21, url: 'https://media.example.test/photo-21.jpg' }] }));
  };
  try {
    const view = renderRoute(account('owner'));
    const [logo, candidate] = await view.findAllByRole('img');
    await act(async () => {
      logo.dispatchEvent(new dom.window.Event('error'));
      candidate.dispatchEvent(new dom.window.Event('error'));
    });
    await waitFor(() => assert.equal(offsets.length, 3));
    await view.findByRole('img', { name: '비공개 밴드 로고를 표시할 수 없음' });
    await view.findByRole('img', { name: '갤러리 사진 #21을 표시할 수 없음' });
    assert.deepEqual(offsets, [0, 0, 0]);
  } finally { globalThis.fetch = previous; }
});

test('stale page response cannot overwrite a logo selected while the read was pending', async () => {
  await load(); const previous = globalThis.fetch; const staleRead = deferred(); let reads = 0; let puts = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'PUT') { puts += 1; return response({ logo: { gallery_image_id: 22, url: null } }); }
    reads += 1;
    if (reads === 2) return staleRead.promise;
    return response(payload({ logoId: 21, logoUrl: null, hasMore: true }));
  };
  try {
    const user = userEvent.setup({ document: dom.window.document });
    const view = renderRoute(account('owner'));
    await view.findByRole('button', { name: '사진 더 불러오기' });
    await user.click(view.getByRole('button', { name: '사진 더 불러오기' }));
    await waitFor(() => assert.equal(reads, 2));
    await user.click(view.getAllByRole('button', { name: '로고로 선택' })[0]);
    await waitFor(() => assert.equal(puts, 1));
    await view.findByRole('button', { name: '로고 해제' });
    assert.ok(view.getByText('갤러리 사진 #22'));
    await act(async () => { staleRead.resolve(response(payload({ logoId: 21, logoUrl: null, gallery: [{ id: 41, url: null }], offset: 20, hasMore: false }))); });
    await view.findByRole('button', { name: '로고 해제' });
    assert.ok(view.getByText('갤러리 사진 #22'));
  } finally { globalThis.fetch = previous; }
});

test('malformed logo update responses show an error instead of silently succeeding', async () => {
  await load(); const previous = globalThis.fetch;
  globalThis.fetch = async (_url, options = {}) => options.method === 'PUT' ? response({}) : response(payload());
  try {
    const view = renderRoute(account('owner'));
    await view.findByRole('heading', { name: '비공개 밴드 로고 관리' });
    await userEvent.click(view.getAllByRole('button', { name: '로고로 선택' })[0]);
    await view.findByRole('alert');
    assert.equal(view.getByRole('alert').textContent, '밴드 로고를 변경하지 못했습니다. 다시 시도해 주세요.');
  } finally { globalThis.fetch = previous; }
});

test('server denial for a non-owner and non-superadmin exposes no candidates or write controls', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({}, 403); };
  try {
    const view = renderRoute(account('band-admin'));
    await view.findByText('이 밴드의 로고를 관리할 권한이 없습니다.');
    assert.equal(view.queryByRole('button', { name: '로고로 선택' }), null);
    assert.equal(view.queryByRole('button', { name: '로고 해제' }), null);
    assert.equal(calls.length, 1);
    assert.ok(calls[0].includes('/logo-management?limit=20&offset=0'));
  } finally { globalThis.fetch = previous; }
});

test('anonymous visitors are gated before the privileged logo endpoint is read', async () => {
  await load(); const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); throw new Error('anonymous endpoint must stay closed'); };
  try {
    const view = renderRoute(account(null), '/bands/private-band/manage/logo', true);
    await view.findByText('로그인 화면');
    assert.deepEqual(calls, []);
  } finally { globalThis.fetch = previous; }
});

test('account switch hides a pending old logo response and ignores its late completion', async () => {
  await load(); const previous = globalThis.fetch; const old = deferred(); let reads = 0;
  globalThis.fetch = async (_url, options = {}) => {
    reads += 1;
    return options.headers?.Authorization === 'Bearer token-account-a' ? old.promise : response(payload({ name: '계정 B 밴드' }));
  };
  const tree = (id) => createElement(auth.AuthContext.Provider, { value: account(id) }, createElement(MemoryRouter, { initialEntries: ['/bands/private-band/manage/logo'] }, createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug/manage/logo', element: createElement(component.BandLogoManagementRoute) }))));
  try {
    const view = render(tree('account-a'));
    await waitFor(() => assert.equal(reads, 1));
    view.rerender(tree('account-b'));
    await view.findByRole('heading', { name: '계정 B 밴드 로고 관리' });
    await act(async () => old.resolve(response(payload({ name: '계정 A 밴드' }))));
    assert.equal(view.queryByRole('heading', { name: '계정 A 밴드 로고 관리' }), null);
  } finally { globalThis.fetch = previous; }
});

test('band switch aborts and hides a late response from the prior logo-management route', async () => {
  await load(); const previous = globalThis.fetch; const old = deferred(); let reads = 0;
  globalThis.fetch = async (url) => {
    reads += 1;
    return String(url).includes('/by-slug/first-band/') ? old.promise : response(payload({ slug: 'second-band', name: '두 번째 밴드' }));
  };
  const tree = createElement(auth.AuthContext.Provider, { value: account('owner') }, createElement(MemoryRouter, { initialEntries: ['/bands/first-band/manage/logo'] }, createElement(Fragment, null,
    createElement(SwitchBand), createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug/manage/logo', element: createElement(component.BandLogoManagementRoute) })))));
  try {
    const view = render(tree);
    await waitFor(() => assert.equal(reads, 1));
    await userEvent.click(view.getByRole('button', { name: '다른 밴드' }));
    await view.findByRole('heading', { name: '두 번째 밴드 로고 관리' });
    await act(async () => old.resolve(response(payload({ slug: 'first-band', name: '첫 번째 밴드' }))));
    assert.equal(view.queryByRole('heading', { name: '첫 번째 밴드 로고 관리' }), null);
  } finally { globalThis.fetch = previous; }
});

test('failed logo image refreshes once and then uses a neutral fallback', async () => {
  await load(); const previous = globalThis.fetch; let reads = 0;
  globalThis.fetch = async () => {
    reads += 1;
    return response(payload({ logoId: 21, logoUrl: reads === 1 ? 'https://media.example.test/logo.jpg' : 'https://media.example.test/refreshed-logo.jpg' }));
  };
  try {
    const view = renderRoute(account('owner'));
    const image = await view.findByRole('img', { name: '비공개 밴드 로고' });
    await act(async () => image.dispatchEvent(new dom.window.Event('error')));
    await waitFor(() => assert.equal(reads, 2));
    const refreshed = await view.findByRole('img', { name: '비공개 밴드 로고' });
    assert.equal(refreshed.getAttribute('src'), 'https://media.example.test/refreshed-logo.jpg');
    await act(async () => refreshed.dispatchEvent(new dom.window.Event('error')));
    await view.findByRole('img', { name: '비공개 밴드 로고를 표시할 수 없음' });
    assert.equal(reads, 2);
  } finally { globalThis.fetch = previous; }
});
