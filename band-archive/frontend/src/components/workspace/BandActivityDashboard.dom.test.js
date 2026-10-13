import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement, Fragment, StrictMode } from 'react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};

let vite; let workspace; let auth; let bands;
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const monthForTest = () => {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit' }).formatToParts(new Date());
  return `${parts.find((part) => part.type === 'year').value}-${parts.find((part) => part.type === 'month').value}`;
};
const dashboardData = (month, overrides = {}) => ({
  band_id: 401,
  month,
  today: `${month}-14`,
  stats: { total_songs: 19, total_rehearsals: 33, status_counts: { Practice: 6, Completed: 7, OnHold: 3, Other: 3 } },
  practice_songs: Array.from({ length: 6 }, (_, index) => ({ id: 501 + index, title: `연습곡 ${index + 1}`, artist: '밴드' })),
  month_rehearsals: { items: [{ id: 601, title: '월간 합주', date: `${month}-14`, songs: [] }], pagination: { total: 1, has_more: false, next_offset: null } },
  upcoming_rehearsals: Array.from({ length: 4 }, (_, index) => ({ id: 701 + index, title: `다가오는 합주 ${index + 1}`, date: `${month}-20`, songs: [] })),
  featured_gallery: { id: 801, url: 'https://media.invalid/featured.jpg' },
  recent_gallery: Array.from({ length: 5 }, (_, index) => ({ id: 811 + index, url: `https://media.invalid/recent-${index + 1}.jpg`, filename: `사진 ${index + 1}` })),
  announcement: { id: 901, content: '이번 달 공지입니다.' },
  ...overrides,
});
const visitor = { user: null, getAccessToken: async () => null, refreshSession: async () => null, signOut: async () => {} };
const load = async () => {
  if (workspace) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  workspace = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx');
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
};
const QueryNavigation = () => {
  const navigate = useNavigate();
  return createElement('button', { type: 'button', onClick: () => navigate('/bands/detail-band/songs?song=502') }, '다른 곡 열기');
};
const ReadOnlyApp = ({ entry, strict = false, queryNavigation = false, authValue = visitor }) => {
  const routes = createElement(Fragment, null,
    queryNavigation && createElement(QueryNavigation),
    createElement(Routes, null,
      createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) },
        createElement(Route, { index: true, element: createElement(workspace.BandDashboard) }),
        createElement(Route, { path: 'songs', element: createElement(workspace.SongWorkspace) }),
        createElement(Route, { path: 'rehearsals', element: createElement(workspace.RehearsalWorkspace) }),
      ),
    ),
  );
  return createElement(auth.AuthContext.Provider, { value: authValue },
    createElement(bands.BandContext.Provider, { value: { activeBands: [] } },
      createElement(MemoryRouter, { initialEntries: [entry] }, strict ? createElement(StrictMode, null, routes) : routes),
    ),
  );
};
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());

test('activity dashboard loads one scoped v2 bundle and bounds each summary section', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url); calls.push({ url: value, method: options.method || 'GET' });
    if (value.includes('/by-slug/activity-band')) return response({ band: { id: 401, slug: 'activity-band', name: '활동 밴드', description: '활동을 모아 봅니다.', membership: null } });
    if (value.includes('/bands/401/dashboard?')) return response({ dashboard: dashboardData(monthForTest()) });
    return response({});
  };
  try {
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/activity-band' }));
    await view.findByText('이번 달 공지입니다.');
    assert.ok(view.getByText('활동을 모아 봅니다.'));
    assert.equal(view.container.querySelector('.activity-stats .activity-stat strong').textContent, '19');
    assert.equal(view.container.querySelectorAll('.activity-stats .activity-stat strong')[1].textContent, '33');
    assert.ok(view.getByText('기타'));
    assert.equal(view.container.querySelectorAll('.activity-practice-list li').length, 5);
    assert.equal(view.container.querySelectorAll('.activity-upcoming .activity-rehearsal').length, 3);
    assert.equal(view.container.querySelectorAll('.activity-gallery-grid img').length, 4);
    assert.ok(view.container.querySelector('.activity-featured').compareDocumentPosition(view.container.querySelector('.activity-stats')) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
    assert.equal(view.getByRole('link', { name: /연습곡 1/ }).getAttribute('href'), '/bands/activity-band/songs?song=501');
    const dashboardCalls = calls.filter(({ url }) => url.includes('/dashboard?'));
    assert.equal(dashboardCalls.length, 1);
    assert.match(dashboardCalls[0].url, new RegExp(`/api/v2/bands/401/dashboard\\?month=${monthForTest()}&limit=25&offset=0`));
    assert.equal(calls.some(({ url }) => url.includes('/songs?') || url.includes('/rehearsals?') || url.includes('/gallery/featured') || /\/api\/[^v]/.test(url)), false);
    assert.equal(view.queryByRole('button', { name: /공지 작성|공지 수정/ }), null);
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('workspace management navigation follows both server permissions, not membership or platform role labels', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const value = String(url);
    if (value.includes('/by-slug/nav-manager')) return response({ band: { id: 401, slug: 'nav-manager', name: '관리 밴드', membership: { role: 'member' }, permissions: { can_manage_members: true, can_manage_invites: true } } });
    if (value.includes('/by-slug/nav-denied')) return response({ band: { id: 402, slug: 'nav-denied', name: '비관리 밴드', membership: { role: 'owner' }, permissions: { can_manage_members: false, can_manage_invites: false }, platform_role: 'superadmin' } });
    if (value.includes('/bands/401/dashboard?')) return response({ dashboard: dashboardData(monthForTest()) });
    if (value.includes('/bands/402/dashboard?')) return response({ dashboard: dashboardData(monthForTest(), { band_id: 402 }) });
    return response({});
  };
  try {
    const manager = render(createElement(ReadOnlyApp, { entry: '/bands/nav-manager' }));
    await manager.findByText('이번 달 공지입니다.');
    assert.equal(manager.getByRole('link', { name: '관리' }).getAttribute('href'), '/bands/nav-manager/manage'); manager.unmount();
    const denied = render(createElement(ReadOnlyApp, { entry: '/bands/nav-denied' }));
    await denied.findByText('이번 달 공지입니다.');
    assert.equal(denied.queryByRole('link', { name: '관리' }), null); denied.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('month pages append updated rows, dedupe ids, and expose missing-row paging', async () => {
  await load(); const previousFetch = globalThis.fetch; const reads = [];
  globalThis.fetch = async (url) => {
    const value = String(url);
    if (value.includes('/by-slug/month-band')) return response({ band: { id: 401, slug: 'month-band', name: '월간 밴드', membership: null } });
    if (value.includes('/bands/401/dashboard?')) {
      const parsed = new URL(value, 'http://localhost'); const month = parsed.searchParams.get('month'); const offset = parsed.searchParams.get('offset'); reads.push({ month, offset });
      if (offset === '25') return response({ dashboard: dashboardData(month, {
        month_rehearsals: { items: [{ id: 601, title: '갱신된 합주', date: `${month}-14` }, { id: 602, title: '두 번째 합주', date: `${month}-14` }], pagination: { total: 26, has_more: false, next_offset: null } },
      }) });
      return response({ dashboard: dashboardData(month, {
        month_rehearsals: { items: [{ id: 601, title: '첫 합주', date: `${month}-14` }], pagination: { total: 26, has_more: true, next_offset: 25 } },
      }) });
    }
    return response({});
  };
  try {
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/month-band' }));
    await view.findByText('1 / 26건 표시');
    assert.ok(view.getByText(/이 달의 일정 일부만 표시 중입니다/));
    await userEvent.click(view.getByRole('button', { name: /이 달 합주 더 불러오기/ }));
    await view.findByText('2 / 26건 표시');
    assert.equal(view.getAllByText('갱신된 합주').length, 1);
    assert.equal(view.getAllByText('두 번째 합주').length, 1);
    assert.deepEqual(reads.map(({ offset }) => offset), ['0', '25']);
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('StrictMode dashboard effect replay still completes the successful scoped load', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const value = String(url);
    if (value.includes('/by-slug/strict-band')) return response({ band: { id: 401, slug: 'strict-band', name: 'Strict 밴드', membership: null } });
    if (value.includes('/bands/401/dashboard?')) return response({ dashboard: dashboardData(monthForTest()) });
    return response({});
  };
  try {
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/strict-band', strict: true }));
    await view.findByText('이번 달 공지입니다.');
    assert.equal(view.queryByText('대시보드를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.'), null);
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('image URL refresh during month append does not cancel or overwrite the appended page', async () => {
  await load(); const previousFetch = globalThis.fetch; const append = deferred(); let dashboardReads = 0;
  globalThis.fetch = async (url) => {
    const value = String(url);
    if (value.includes('/by-slug/append-image-band')) return response({ band: { id: 401, slug: 'append-image-band', name: '페이지 밴드', membership: null } });
    if (value.includes('/bands/401/dashboard?')) {
      const parsed = new URL(value, 'http://localhost'); const month = parsed.searchParams.get('month'); const offset = parsed.searchParams.get('offset');
      if (offset === '25') return append.promise;
      dashboardReads += 1;
      const imageUrl = dashboardReads > 1 ? 'https://media.invalid/append-fresh.jpg' : 'https://media.invalid/append-expired.jpg';
      return response({ dashboard: dashboardData(month, { featured_gallery: { id: 801, url: imageUrl }, month_rehearsals: { items: [{ id: 601, title: '첫 페이지 합주', date: `${month}-14` }], pagination: { total: 2, has_more: true, next_offset: 25 } } }) });
    }
    return response({});
  };
  try {
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/append-image-band' }));
    await view.findByText('1 / 2건 표시');
    await userEvent.click(view.getByRole('button', { name: /이 달 합주 더 불러오기/ }));
    await waitFor(() => assert.equal(view.getByRole('button', { name: /합주를 불러오는 중/ }).disabled, true));
    fireEvent.error(view.getByAltText('대표 갤러리 사진'));
    await waitFor(() => assert.equal(view.getByAltText('대표 갤러리 사진').getAttribute('src'), 'https://media.invalid/append-fresh.jpg'));
    append.resolve(response({ dashboard: dashboardData(monthForTest(), {
      month_rehearsals: { items: [{ id: 602, title: '추가 페이지 합주', date: `${monthForTest()}-14` }], pagination: { total: 2, has_more: false, next_offset: null } },
    }) }));
    await view.findByText('2 / 2건 표시');
    assert.equal(view.getByText('추가 페이지 합주').textContent, '추가 페이지 합주');
    assert.equal(view.queryByRole('button', { name: /이 달 합주 더 불러오기/ }), null);
    view.unmount();
  } finally { append.resolve(response({ dashboard: dashboardData(monthForTest()) })); globalThis.fetch = previousFetch; }
});

test('image refresh happens once per workspace/month/image and placeholder is non-blocking', async () => {
  await load(); const previousFetch = globalThis.fetch; let dashboardReads = 0;
  globalThis.fetch = async (url) => {
    const value = String(url);
    if (value.includes('/by-slug/image-band')) return response({ band: { id: 401, slug: 'image-band', name: '사진 밴드', membership: null } });
    if (value.includes('/bands/401/dashboard?')) {
      dashboardReads += 1;
      const imageUrl = dashboardReads > 1 ? 'https://media.invalid/fresh.jpg' : 'https://media.invalid/expired.jpg';
      return response({ dashboard: dashboardData(monthForTest(), { featured_gallery: { id: 801, url: imageUrl } }) });
    }
    return response({});
  };
  try {
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/image-band' }));
    fireEvent.error(await view.findByAltText('대표 갤러리 사진'));
    await waitFor(() => assert.equal(view.getByAltText('대표 갤러리 사진').getAttribute('src'), 'https://media.invalid/fresh.jpg'));
    fireEvent.error(view.getByAltText('대표 갤러리 사진'));
    await waitFor(() => assert.ok(view.container.querySelector('.activity-image-placeholder-featured')));
    assert.equal(dashboardReads, 2);
    assert.ok(view.getByText('이번 달 공지입니다.'));
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('dashboard ignores a late previous-account bundle after an in-place account switch', async () => {
  await load(); const previousFetch = globalThis.fetch; const firstBundle = deferred(); let bandReads = 0;
  globalThis.fetch = async (url) => {
    const value = String(url);
    if (value.includes('/by-slug/account-dashboard')) {
      bandReads += 1;
      return response({ band: { id: bandReads === 1 ? 401 : 402, slug: 'account-dashboard', name: `계정 ${bandReads}`, membership: null } });
    }
    if (value.includes('/bands/401/dashboard?')) return firstBundle.promise;
    if (value.includes('/bands/402/dashboard?')) return response({ dashboard: dashboardData(monthForTest(), { band_id: 402, announcement: { id: 902, content: '현재 계정 대시보드' } }) });
    return response({});
  };
  const account = (id) => ({ user: { id }, getAccessToken: async () => `token-${id}`, refreshSession: async () => `token-${id}`, signOut: async () => {} });
  try {
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/account-dashboard', authValue: account('a') }));
    await waitFor(() => assert.equal(bandReads, 1));
    view.rerender(createElement(ReadOnlyApp, { entry: '/bands/account-dashboard', authValue: account('b') }));
    await view.findByText('현재 계정 대시보드');
    await act(async () => { firstBundle.resolve(response({ dashboard: dashboardData(monthForTest(), { announcement: { id: 901, content: '이전 계정 대시보드' } }) })); });
    assert.equal(view.queryByText('이전 계정 대시보드'), null);
    assert.ok(view.getByText('현재 계정 대시보드'));
    view.unmount();
  } finally { firstBundle.resolve(response({ dashboard: dashboardData(monthForTest()) })); globalThis.fetch = previousFetch; }
});

test('a late by-id deep-link response cannot replace a newer selected song', async () => {
  await load(); const previousFetch = globalThis.fetch; const firstDetail = deferred(); const detailReads = [];
  globalThis.fetch = async (url) => {
    const value = String(url);
    if (value.includes('/by-slug/detail-band')) return response({ band: { id: 401, slug: 'detail-band', name: '상세 밴드', membership: null } });
    if (value.endsWith('/bands/401/songs?')) return response({ songs: [], pagination: { has_more: false } });
    if (value.endsWith('/bands/401/songs/501')) { detailReads.push('501'); return firstDetail.promise; }
    if (value.endsWith('/bands/401/songs/502')) { detailReads.push('502'); return response({ song: { id: 502, title: '새 선택 곡', artist: '밴드', band_id: 401 } }); }
    return response({});
  };
  try {
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/detail-band/songs?song=501', queryNavigation: true }));
    await waitFor(() => assert.deepEqual(detailReads, ['501']));
    await userEvent.click(view.getByRole('button', { name: '다른 곡 열기' }));
    await view.findByText('새 선택 곡');
    await act(async () => { firstDetail.resolve(response({ song: { id: 501, title: '이전 선택 곡', artist: '밴드', band_id: 401 } })); });
    assert.equal(view.queryByText('이전 선택 곡'), null);
    assert.ok(view.getByText('새 선택 곡'));
    view.unmount();
  } finally { firstDetail.resolve(response({ song: { id: 501, title: '이전 선택 곡', band_id: 401 } })); globalThis.fetch = previousFetch; }
});

test('dashboard links load authoritative song and rehearsal details by strict positive id', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => {
    const value = String(url); calls.push(value);
    if (value.includes('/by-slug/detail-band')) return response({ band: { id: 401, slug: 'detail-band', name: '상세 밴드', membership: null } });
    if (value.includes('/bands/401/dashboard?')) return response({ dashboard: dashboardData(monthForTest(), {
      practice_songs: [{ id: 501, title: '원본 연습곡', artist: '밴드' }],
      upcoming_rehearsals: [{ id: 701, title: '원본 합주', date: `${monthForTest()}-20`, songs: [{ id: 501, title: '원본 연습곡', status: 'Practice' }], has_more_songs: true }],
    }) });
    if (value.includes('/bands/401/songs?')) return response({ songs: [], pagination: { has_more: false } });
    if (value.endsWith('/bands/401/songs/501')) return response({ song: { id: 501, title: '권위 있는 곡 상세', artist: '밴드', band_id: 401 } });
    if (value.includes('/bands/401/rehearsals?')) return response({ rehearsals: [], pagination: { has_more: false } });
    if (value.endsWith('/bands/401/rehearsals/701')) return response({ rehearsal: { id: 701, title: '권위 있는 합주 상세', date: '2026-10-20', band_id: 401, songs: [] } });
    return response({});
  };
  try {
    const songView = render(createElement(ReadOnlyApp, { entry: '/bands/detail-band' }));
    await waitFor(() => assert.ok(songView.container.querySelector('a[href="/bands/detail-band/songs?song=501"]')));
    await userEvent.click(songView.container.querySelector('a[href="/bands/detail-band/songs?song=501"]'));
    await songView.findByText('권위 있는 곡 상세');
    assert.ok(calls.some((value) => value.endsWith('/api/v2/bands/401/songs/501')));
    songView.unmount();

    const rehearsalView = render(createElement(ReadOnlyApp, { entry: '/bands/detail-band' }));
    await waitFor(() => assert.ok(rehearsalView.container.querySelector('a[href="/bands/detail-band/rehearsals?rehearsal=701"]')));
    await userEvent.click(rehearsalView.container.querySelector('a[href="/bands/detail-band/rehearsals?rehearsal=701"]'));
    await rehearsalView.findByText('권위 있는 합주 상세');
    assert.ok(calls.some((value) => value.endsWith('/api/v2/bands/401/rehearsals/701')));
    rehearsalView.unmount();

    const invalidView = render(createElement(ReadOnlyApp, { entry: '/bands/detail-band/songs?song=01' }));
    await invalidView.findByRole('alert');
    assert.equal(calls.some((value) => /\/songs\/01(?:\?|$)/.test(value)), false);
    invalidView.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('truncated announcement expands through the scoped read endpoint only when requested', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => {
    const value = String(url); calls.push(value);
    if (value.includes('/by-slug/notice-dashboard')) return response({ band: { id: 401, slug: 'notice-dashboard', name: '공지 밴드', membership: null } });
    if (value.includes('/bands/401/dashboard?')) return response({ dashboard: dashboardData(monthForTest(), { announcement: { id: 901, content: '앞부분', content_truncated: true, private_key: 'never render' } }) });
    if (value.endsWith('/bands/401/announcement')) return response({ announcement: { id: 901, content: '전체 공지 내용' } });
    return response({});
  };
  try {
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/notice-dashboard' }));
    await view.findByText('앞부분');
    assert.equal(calls.some((value) => value.endsWith('/announcement')), false);
    assert.equal(view.queryByText('never render'), null);
    await userEvent.click(view.getByRole('button', { name: '전체 공지 보기' }));
    await view.findByText('전체 공지 내용');
    assert.equal(calls.filter((value) => value.endsWith('/announcement')).length, 1);
    assert.equal(view.queryByRole('button', { name: /공지 작성|공지 수정/ }), null);
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});
