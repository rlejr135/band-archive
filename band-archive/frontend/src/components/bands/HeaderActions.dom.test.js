import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { createElement } from 'react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
let vite; let auth; let bands; let actions;
const load = async () => {
  if (actions) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
  actions = await vite.ssrLoadModule('/src/components/layout/HeaderActions.jsx');
};
const Location = () => {
  const location = useLocation();
  const navigate = useNavigate();
  return createElement('div', null,
    createElement('output', { 'data-testid': 'location' }, location.pathname),
    createElement('button', { type: 'button', onClick: () => navigate('/guide') }, '다른 경로 이동'),
    createElement('button', { type: 'button', onClick: () => navigate(-1) }, '뒤로 이동'));
};
const mount = async (bandState = {}, authState = {}) => {
  await load();
  return render(createElement(auth.AuthContext.Provider, { value: { user: { id: 'synthetic-user' }, loading: false, signOut: async () => {}, ...authState } }, createElement(bands.BandContext.Provider, { value: { activeBands: [], loading: false, error: '', ...bandState } }, createElement(MemoryRouter, { initialEntries: ['/portal'] }, createElement(actions.default, { accountMode: true }), createElement(Location)))));
};
afterEach(() => cleanup());
after(async () => { await vite?.close(); dom.window.close(); });

test('loaded empty and pending-only memberships provide a nonbusy band discovery link', async () => {
  const view = await mount({ pendingMemberships: [{ id: 4 }] });
  assert.ok(view.getByText('가입한 밴드 없음'));
  assert.equal(view.queryByRole('combobox'), null);
  assert.equal(view.container.querySelector('.band-switcher').getAttribute('aria-busy'), 'false');
  fireEvent.click(view.getByRole('link', { name: /가입한 밴드 없음.*밴드 찾기/ }));
  assert.equal(view.getByTestId('location').textContent, '/bands/discover');
});

test('loading and error are separate states and errors expose an explicit retry', async () => {
  let retries = 0;
  const view = await mount({ loading: true });
  assert.equal(view.getByRole('combobox').disabled, true);
  assert.ok(view.getByText('밴드 목록 불러오는 중'));
  cleanup();
  const failed = await mount({ error: 'network', reload: () => { retries += 1; } });
  assert.equal(failed.container.querySelector('.band-switcher').getAttribute('aria-busy'), 'false');
  assert.ok(failed.getByText('밴드 목록 확인 필요'));
  fireEvent.click(failed.getByRole('button', { name: '목록 다시 불러오기' }));
  assert.equal(retries, 1);
});

test('account disclosure closes on Escape and outside press and keeps logout operational', async () => {
  let signouts = 0;
  const view = await mount({}, { signOut: async () => { signouts += 1; } });
  const trigger = view.getByRole('button', { name: '계정 메뉴' });
  fireEvent.click(trigger);
  assert.equal(trigger.getAttribute('aria-expanded'), 'true');
  assert.equal(view.getByRole('link', { name: '홈으로' }).getAttribute('href'), '/portal');
  fireEvent.keyDown(document, { key: 'Escape' });
  assert.equal(trigger.getAttribute('aria-expanded'), 'false');
  assert.equal(document.activeElement, trigger);
  fireEvent.click(trigger); fireEvent.pointerDown(document.body);
  assert.equal(trigger.getAttribute('aria-expanded'), 'false');
  fireEvent.click(trigger); fireEvent.click(view.getByRole('button', { name: '로그아웃' }));
  await view.findByText('/');
  assert.equal(signouts, 1);
});

test('active band selection preserves the band id selection and slug route', async () => {
  let selected;
  const view = await mount({ activeBands: [{ id: 9, name: '아주 긴 밴드 이름', slug: 'long-band' }], selectBand: (id) => { selected = id; } });
  fireEvent.change(view.getByRole('combobox', { name: '빠른 밴드 전환' }), { target: { value: '9' } });
  assert.equal(selected, 9);
  assert.equal(view.getByTestId('location').textContent, '/bands/long-band');
});

test('leaving a route and returning through history does not reopen its old account disclosure', async () => {
  const view = await mount();
  fireEvent.click(view.getByRole('button', { name: '계정 메뉴' }));
  assert.equal(view.getByRole('button', { name: '계정 메뉴' }).getAttribute('aria-expanded'), 'true');
  fireEvent.click(view.getByRole('button', { name: '다른 경로 이동' }));
  assert.equal(view.getByTestId('location').textContent, '/guide');
  assert.equal(view.getByRole('button', { name: '계정 메뉴' }).getAttribute('aria-expanded'), 'false');
  fireEvent.click(view.getByRole('button', { name: '뒤로 이동' }));
  assert.equal(view.getByTestId('location').textContent, '/portal');
  assert.equal(view.getByRole('button', { name: '계정 메뉴' }).getAttribute('aria-expanded'), 'false');
  assert.equal(view.queryByRole('button', { name: '로그아웃' }), null);
});
