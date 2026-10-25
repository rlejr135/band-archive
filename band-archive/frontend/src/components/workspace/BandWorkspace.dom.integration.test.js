/* eslint-disable react-hooks/globals */
import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
// React's legacy input-event fallback probes these IE methods in jsdom.
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};

let vite; let workspace; let songScreen; let rehearsalScreen; let auth; let bands; let policies; let members; let memberDetail;
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const load = async () => { if (workspace) return; vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); workspace = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx'); songScreen = await vite.ssrLoadModule('/src/components/workspace/songs/SongWorkspace.jsx'); rehearsalScreen = await vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalWorkspace.jsx'); members = await vite.ssrLoadModule('/src/components/workspace/members/MembersWorkspace.jsx'); memberDetail = await vite.ssrLoadModule('/src/components/workspace/members/MemberDetailWorkspace.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); bands = await vite.ssrLoadModule('/src/context/BandContext.jsx'); policies = await vite.ssrLoadModule('/src/context/ServicePolicyContext.jsx'); };
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());

// The existing API-contract fixtures explicitly cover the disabled rollout.
// Enabled and unavailable policy behavior has its own real-form DOM cases.
const App = ({ entry, authValue, bandValue, songs = false, rehearsals = false }) => createElement(policies.ServicePolicyContext.Provider, { value: { config: { policies: { upload: { enabled: false } } } } }, createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: bandValue }, createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) }, songs ? createElement(Route, { path: 'songs', element: createElement(songScreen.SongWorkspace) }) : null, rehearsals ? createElement(Route, { path: 'rehearsals', element: createElement(rehearsalScreen.RehearsalWorkspace) }) : null))))));
const ReadOnlyApp = ({ entry, authValue, bandValue = { activeBands: [] } }) => createElement(
  auth.AuthContext.Provider, { value: authValue },
  createElement(bands.BandContext.Provider, { value: bandValue },
    createElement(MemoryRouter, { initialEntries: [entry] },
      createElement(Routes, null,
        createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) },
          createElement(Route, { index: true, element: createElement(workspace.BandDashboard) }),
          createElement(Route, { path: 'members', element: createElement(members.MembersWorkspace) }),
          createElement(Route, { path: 'members/:memberId', element: createElement(memberDetail.MemberDetailWorkspace) }),
          createElement(Route, { path: 'gallery', element: createElement(workspace.GalleryWorkspace) }),
          createElement(Route, { path: 'suggestions', element: createElement(workspace.SuggestionsWorkspace) }),
          createElement(Route, { path: 'songs', element: createElement(songScreen.SongWorkspace) }),
          createElement(Route, { path: 'rehearsals', element: createElement(rehearsalScreen.RehearsalWorkspace) }),
        ),
      ),
    ),
  ),
);

test('active member roster marks self, edits optional profile and refreshes cards without administrator edits', async () => {
  await load(); const previousFetch = globalThis.fetch; let listReads = 0; const writes = [];
  let mine = { id: 41, band_id: 340, name: '내 이름', biography: '', positions: [], profile_revision: 0, custom_position: '', is_self: true, can_edit_profile: true, can_delete: false };
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/profile-band')) return response({ band: { id: 340, slug: 'profile-band', name: '프로필 밴드', permissions: { can_view_roster: true }, membership: { id: 1, status: 'ACTIVE', role: 'owner' } } });
    if (url.includes('/bands/340/members?')) { listReads += 1; return response({ members: [mine, { id: 42, name: '다른 멤버', biography: '다른 소개', positions: ['DRUMS'], is_self: false, can_edit_profile: false }], pagination: { has_more: false } }); }
    if (url.endsWith('/bands/340/members/me')) {
      if (options.method === 'PATCH') { writes.push(JSON.parse(options.body)); mine = { ...mine, ...writes.at(-1), profile_revision: 1 }; }
      return response({ member: mine });
    }
    return response({});
  };
  try {
    const member = { user: { id: 'self' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/profile-band/members', authValue: member }));
    await view.findByText('내 이름'); assert.equal(view.getAllByText('나').length, 1); assert.equal(view.getAllByRole('button', { name: '내 프로필 수정' }).length, 2);
    assert.match(view.getByText('다른 멤버').closest('article').textContent, /드럼/);
    assert.equal(view.getByText('다른 멤버').closest('article').querySelector('button'), null);
    await userEvent.click(view.getAllByRole('button', { name: '내 프로필 수정' })[0]);
    await view.findByLabelText('밴드에서 사용할 이름'); await userEvent.setup({ document: dom.window.document }).clear(view.getByLabelText('밴드에서 사용할 이름')); await userEvent.type(view.getByLabelText('밴드에서 사용할 이름'), '새 밴드 이름');
    await view.findByLabelText('간단한 소개'); await userEvent.type(view.getByLabelText('간단한 소개'), '함께 연주해요');
    await userEvent.click(view.getByRole('checkbox', { name: '베이스', exact: true }));
    await userEvent.click(view.getByRole('button', { name: '저장', exact: true }));
    await view.findByText('내 프로필을 저장했습니다.'); await view.findByText('함께 연주해요'); await view.findByText('새 밴드 이름');
    assert.equal(listReads, 2); assert.equal(writes.length, 1); assert.deepEqual(writes[0], { name: '새 밴드 이름', biography: '함께 연주해요', positions: ['BASS'], profile_revision: 0 });
  } finally { globalThis.fetch = previousFetch; }
});

test('self detail updates its biography immediately while other member profile remains read only', async () => {
  await load(); const previousFetch = globalThis.fetch;
  let mine = { id: 51, band_id: 341, name: '내 상세', biography: '기존 소개', positions: ['VOCAL'], profile_revision: 1, custom_position: '', is_self: true, can_edit_profile: true, can_delete: false };
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/profile-detail')) return response({ band: { id: 341, slug: 'profile-detail', name: '상세 밴드', permissions: { can_view_roster: true }, membership: { id: 1, status: 'ACTIVE' } } });
    if (url.includes('/logs?')) return response({ logs: [], pagination: { has_more: false } });
    if (url.endsWith('/members/me')) { if (options.method === 'PATCH') mine = { ...mine, ...JSON.parse(options.body), profile_revision: 2 }; return response({ member: mine }); }
    if (url.endsWith('/members/51')) return response({ member: mine });
    if (url.endsWith('/members/52')) return response({ member: { id: 52, name: '읽기 전용', biography: '다른 사람', positions: ['KEYBOARD'], is_self: false, can_edit_profile: false, membership_status: 'inactive' } });
    return response({});
  };
  try {
    const member = { user: { id: 'self' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/profile-detail/members/51', authValue: member }));
    await view.findByText('기존 소개'); await userEvent.click(view.getByRole('button', { name: '내 프로필 수정' }));
    const user = userEvent.setup({ document: dom.window.document });
    await view.findByLabelText('밴드에서 사용할 이름'); await user.clear(view.getByLabelText('밴드에서 사용할 이름')); await user.type(view.getByLabelText('밴드에서 사용할 이름'), '상세 이름 변경');
    await view.findByLabelText('간단한 소개'); await user.clear(view.getByLabelText('간단한 소개')); await user.type(view.getByLabelText('간단한 소개'), '변경한 소개');
    await userEvent.click(view.getByRole('button', { name: '저장', exact: true })); await view.findByText('변경한 소개'); await view.findByText('상세 이름 변경'); assert.equal(view.queryByRole('button', { name: '삭제' }), null);
    view.unmount();
    const other = render(createElement(ReadOnlyApp, { entry: '/bands/profile-detail/members/52', authValue: member }));
    await other.findByText('다른 사람'); await other.findByText(/현재 밴드 멤버가 아닙니다/); assert.equal(other.queryByRole('button', { name: '내 프로필 수정' }), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('visitor roster denial is non-inferential and does not expose a member navigation link', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => { calls.push({ url, method: options.method || 'GET' }); if (url.includes('/by-slug/hidden-roster')) return response({ band: { id: 301, slug: 'hidden-roster', name: '공개 밴드', permissions: { can_view_roster: false }, membership: null } }); if (url.includes('/members?')) return response({}, 404); if (url.includes('/gallery?') || url.includes('/suggestions?')) return response({ pagination: { has_more: false } }); return response({}); };
  try { const visitor = { user: null, getAccessToken: async () => null, refreshSession: async () => null, signOut: async () => {} }; const view = render(createElement(ReadOnlyApp, { entry: '/bands/hidden-roster/members', authValue: visitor })); await view.findByText('요청한 밴드 정보는 현재 표시할 수 없습니다.'); assert.equal(view.queryByRole('link', { name: '멤버' }), null); assert.equal(calls.some((call) => call.method !== 'GET'), false); view.unmount(); } finally { globalThis.fetch = previousFetch; }
});

test('member logs paginate, play a signed URL, and refresh it once after media failure', async () => {
  await load(); const previousFetch = globalThis.fetch; let logReads = 0;
  globalThis.fetch = async (url) => { if (url.includes('/by-slug/log-band')) return response({ band: { id: 302, slug: 'log-band', name: '기록 밴드', permissions: { can_view_roster: true }, membership: { id: 1, status: 'ACTIVE' } } }); if (url.includes('/members/7/logs?')) { if (url.includes('offset=20')) return response({ logs: [{ id: 72, title: '다음 영상', file_type: 'video', playback: { video_url: 'https://media.invalid/next.mp4' } }], pagination: { has_more: false } }); logReads += 1; return response({ logs: [{ id: 71, title: '첫 영상', file_type: 'video', playback: { video_url: 'https://media.invalid/first.mp4' } }], pagination: { has_more: true, next_offset: 20 } }); } if (url.endsWith('/members/7')) return response({ member: { id: 7, name: '드러머', instrument: '드럼' } }); if (url.includes('/personal-logs/71/processing')) return response({ processing: { status: 'completed' } }); if (url.includes('/gallery?') || url.includes('/suggestions?')) return response({ pagination: { has_more: false } }); return response({}); };
  try { const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} }; const view = render(createElement(ReadOnlyApp, { entry: '/bands/log-band/members/7', authValue: member })); await view.findByText('첫 영상'); await userEvent.click(view.getByText('첫 영상')); const video = await waitFor(() => { const item = view.container.querySelector('video'); assert.ok(item); return item; }); fireEvent.error(video); fireEvent.error(video); await waitFor(() => assert.equal(logReads, 2)); await userEvent.click(view.getByRole('button', { name: '기록 더 불러오기' })); await view.findByText('다음 영상'); view.unmount(); } finally { globalThis.fetch = previousFetch; }
});

test('workspace header uses the current band logo, refreshes one failed URL, then shows a neutral fallback', async () => {
  await load(); const previousFetch = globalThis.fetch; let bandReads = 0;
  globalThis.fetch = async (url) => {
    if (String(url).includes('/by-slug/header-logo')) {
      bandReads += 1;
      return response({ band: { id: 350, slug: 'header-logo', name: '브랜드 밴드', logo: { gallery_image_id: 70, url: bandReads === 1 ? 'https://media.example.test/logo.jpg' : 'https://media.example.test/logo-refreshed.jpg' }, permissions: {}, membership: { status: 'active', role: 'member' } } });
    }
    throw new Error(`unexpected ${String(url)}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/header-logo', authValue: { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token' }, bandValue: { activeBands: [] } }));
    const image = await view.findByRole('img', { name: '브랜드 밴드 로고' });
    assert.equal(image.getAttribute('src'), 'https://media.example.test/logo.jpg');
    await act(async () => image.dispatchEvent(new dom.window.Event('error')));
    await waitFor(() => assert.equal(bandReads, 2));
    const refreshed = await view.findByRole('img', { name: '브랜드 밴드 로고' });
    assert.equal(refreshed.getAttribute('src'), 'https://media.example.test/logo-refreshed.jpg');
    await act(async () => refreshed.dispatchEvent(new dom.window.Event('error')));
    await view.findByText('♫');
    assert.equal(bandReads, 2);
    assert.equal(view.container.querySelector('.workspace-brand-mark') != null, true);
  } finally { globalThis.fetch = previousFetch; }
});

test('gallery and suggestions retain paged safe reads, featured image refresh, and read-only controls', async () => {
  await load(); const previousFetch = globalThis.fetch; let galleryReads = 0; let featuredReads = 0;
  globalThis.fetch = async (url) => { if (url.includes('/by-slug/read-band')) return response({ band: { id: 303, slug: 'read-band', name: '읽기 밴드', permissions: { can_view_roster: false }, membership: null } }); if (url.includes('/gallery/featured')) { featuredReads += 1; return response({ gallery: { id: 80, url: `https://media.invalid/featured-${featuredReads}.jpg` } }); } if (url.includes('/gallery?')) { if (url.includes('offset=20')) return response({ gallery: [{ id: 82, url: 'https://media.invalid/two.jpg' }], pagination: { has_more: false } }); galleryReads += 1; return response({ gallery: [{ id: 81, url: 'https://media.invalid/one.jpg' }], pagination: { has_more: true, next_offset: 20 } }); } if (url.includes('/suggestions?')) return response({ suggestions: [{ id: 91, title: '읽기 추천곡', artist: '밴드', vote_score: 4, status: 'pending', memo: '연습용' }], pagination: { has_more: false } }); return response({}); };
  try { const visitor = { user: null, getAccessToken: async () => null, refreshSession: async () => null, signOut: async () => {} }; const gallery = render(createElement(ReadOnlyApp, { entry: '/bands/read-band/gallery', authValue: visitor })); await gallery.findByRole('heading', { name: '대표 사진' }); const featured = gallery.getByAltText('대표 갤러리 이미지'); fireEvent.error(featured); fireEvent.error(featured); await waitFor(() => assert.equal(featuredReads, 2)); fireEvent.error(gallery.getByAltText('갤러리 이미지')); await waitFor(() => assert.ok(galleryReads >= 2)); await userEvent.click(gallery.getByRole('button', { name: '사진 더 불러오기' })); await waitFor(() => assert.equal(gallery.getAllByAltText('갤러리 이미지').length, 2)); gallery.unmount(); const suggestions = render(createElement(ReadOnlyApp, { entry: '/bands/read-band/suggestions', authValue: visitor })); await suggestions.findByText('읽기 추천곡'); assert.equal(suggestions.queryByRole('button', { name: /추천|투표|추가|생성/ }), null); assert.equal(suggestions.queryByRole('group', { name: '추천곡 투표' }), null); assert.equal(suggestions.queryByRole('group', { name: '추천곡 관리' }), null); assert.ok(suggestions.getByText(/점수 4/)); assert.ok(suggestions.getByText('검토 대기')); suggestions.unmount(); } finally { globalThis.fetch = previousFetch; }
});

test('gallery owner deletion uses the async deletion task and never displays actions without can_delete', async () => {
  await load(); const previousFetch = globalThis.fetch; const previousConfirm = window.confirm; const calls = []; let rows = [{ id: 84, filename: '풍경.jpg', url: 'https://media.invalid/landscape.jpg', can_delete: true }, { id: 85, filename: '보호 사진.jpg', url: 'https://media.invalid/protected.jpg', can_delete: false }]; window.confirm = () => true;
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); const method = options.method || 'GET'; calls.push({ path, method });
    if (path.includes('/by-slug/gallery-owner')) return response({ band: { id: 319, slug: 'gallery-owner', name: '사진 밴드', membership: { id: 9, role: 'owner', status: 'active' }, permissions: {} } });
    if (path.includes('/bands/319/gallery/featured')) return response({}, 404);
    if (path.includes('/bands/319/gallery?')) return response({ gallery: rows, pagination: { total: rows.length, has_more: false } });
    if (path.endsWith('/bands/319/gallery/84') && method === 'DELETE') { rows = []; return response({ task_id: 'gallery-task', status: 'queued', retry_after_seconds: 0 }); }
    if (path.endsWith('/bands/319/deletion-tasks/gallery-task')) return response({ task_id: 'gallery-task', status: 'completed', resource_type: 'gallery', resource_id: 84 });
    throw new Error(`unexpected ${method} ${path}`);
  };
  try {
    const member = { user: { id: 'gallery-owner' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/gallery-owner/gallery', authValue: member }));
    await view.findAllByAltText('갤러리 이미지');
    assert.equal(view.getAllByRole('button', { name: '삭제' }).length, 1);
    await userEvent.click(view.getByRole('button', { name: '삭제' }));
    await view.findByText('표시할 사진이 없습니다.');
    assert.deepEqual(calls.filter((call) => call.method !== 'GET').map(({ path, method }) => ({ path: path.replace(/^https?:\/\/[^/]+/, ''), method })), [{ path: '/api/v2/bands/319/gallery/84', method: 'DELETE' }]);
    assert.equal(calls.some((call) => call.path.endsWith('/bands/319/deletion-tasks/gallery-task')), true);
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('dashboard renders only the bounded public announcement summary and exposes no management controls', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/notice-empty')) return response({ band: { id: 304, slug: 'notice-empty', name: '빈 공지 밴드', permissions: {}, membership: null } });
    if (url.includes('/by-slug/notice-visible')) return response({ band: { id: 305, slug: 'notice-visible', name: '공지 밴드', permissions: {}, membership: null } });
    if (url.includes('/bands/304/dashboard?')) return response({ dashboard: { band_id: 304, month: new URL(url, 'http://localhost').searchParams.get('month'), stats: {}, month_rehearsals: { items: [], pagination: { total: 0, has_more: false } }, announcement: null } });
    if (url.includes('/bands/305/dashboard?')) return response({ dashboard: { band_id: 305, month: new URL(url, 'http://localhost').searchParams.get('month'), stats: {}, month_rehearsals: { items: [], pagination: { total: 0, has_more: false } }, announcement: { id: 1, content: '이번 주 합주는 토요일입니다.', email: 'private@example.invalid', object_key: 'private/key' } } });
    return response({});
  };
  try {
    const visitor = { user: null, getAccessToken: async () => null, refreshSession: async () => null, signOut: async () => {} };
    const empty = render(createElement(ReadOnlyApp, { entry: '/bands/notice-empty', authValue: visitor }));
    await empty.findByText('현재 공개된 공지사항이 없습니다.');
    empty.unmount();
    const visible = render(createElement(ReadOnlyApp, { entry: '/bands/notice-visible', authValue: visitor }));
    await visible.findByText('이번 주 합주는 토요일입니다.');
    assert.equal(visible.queryByText('private@example.invalid'), null);
    assert.equal(visible.queryByText('private/key'), null);
    assert.equal(visible.queryByRole('button', { name: /공지 작성|공지 수정/ }), null);
    visible.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('dashboard does not expose role-management UI or issue legacy list reads', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/owner-band')) return response({ band: { id: 321, slug: 'owner-band', name: '소유자 밴드', membership: { id: 91, role: 'owner' }, permissions: {} } });
    if (url.includes('/bands/321/dashboard?')) return response({ dashboard: { band_id: 321, month: new URL(url, 'http://localhost').searchParams.get('month'), stats: {}, month_rehearsals: { items: [], pagination: { total: 0, has_more: false } }, announcement: null } });
    throw new Error(`unexpected dashboard request ${url}`);
  };
  try {
    const member = { user: { id: 'owner' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/owner-band', authValue: member }));
    await view.findByText('대표 사진이 없습니다');
    assert.equal(view.queryByText('현재 접근 권한'), null);
    assert.equal(view.queryByText('소유자'), null);
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('suggestions render only sanitized HTTP(S) links as external anchors', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/link-band')) return response({ band: { id: 306, slug: 'link-band', name: '링크 밴드', permissions: {}, membership: null } });
    if (url.includes('/suggestions?')) return response({ suggestions: [
      { id: 1, title: 'HTTPS', artist: '밴드', link: 'https://example.com/song' },
      { id: 2, title: 'HTTP', artist: '밴드', link: 'http://example.com/song' },
      { id: 3, title: 'Script', artist: '밴드', link: 'javascript:alert(1)' },
      { id: 4, title: 'Data', artist: '밴드', link: 'data:text/html,boom' },
      { id: 5, title: 'Protocol relative', artist: '밴드', link: '//example.com/song' },
      { id: 6, title: 'Credentials', artist: '밴드', link: 'https://user:pass@example.com/song' },
      { id: 7, title: 'Malformed', artist: '밴드', link: 'https://' },
    ], pagination: { has_more: false } });
    return response({});
  };
  try {
    const visitor = { user: null, getAccessToken: async () => null, refreshSession: async () => null, signOut: async () => {} };
    const view = render(createElement(ReadOnlyApp, { entry: '/bands/link-band/suggestions', authValue: visitor }));
    await view.findByText('HTTPS');
    const links = view.getAllByRole('link', { name: '참고 링크 열기' });
    assert.deepEqual(links.map((link) => link.getAttribute('href')), ['https://example.com/song', 'http://example.com/song']);
    links.forEach((link) => { assert.equal(link.getAttribute('target'), '_blank'); assert.equal(link.getAttribute('rel'), 'noopener noreferrer'); });
    ['Script', 'Data', 'Protocol relative', 'Credentials', 'Malformed'].forEach((title) => {
      assert.equal([...view.getByText(title).closest('article').querySelectorAll('a')].length, 0);
    });
    assert.equal(view.container.querySelector('a[href^="javascript:"],a[href^="data:"],a[href^="//"]'), null);
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('DOM route navigation drops a genuinely pending A resolution without mutating rendered B state', async () => {
  await load(); const previousFetch = globalThis.fetch; const a = deferred(); const b = deferred(); const calls = []; let aSettled = false; let navigate;
  a.promise.then(() => { aSettled = true; });
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/band-a')) { calls.push('A band'); return a.promise; }
    if (url.includes('/by-slug/band-b')) { calls.push('B band'); return b.promise; }
    if (url.includes('/bands/2/songs?')) { calls.push('B songs'); return response({ songs: [{ id: 21, title: 'B 선택 곡', artist: 'B', can_edit: true }], pagination: { has_more: false } }); }
    if (url.includes('/songs/21/media?')) { calls.push('B media'); return response({ media: [], pagination: { has_more: false } }); }
    throw new Error(`unexpected request ${url}`);
  };
  const Go = () => { navigate = useNavigate(); return null; }; const authValue = { user: { id: 'account-a' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  const tree = createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: { activeBands: [] } }, createElement(MemoryRouter, { initialEntries: ['/bands/band-a/songs'] }, createElement(Go), createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) }, createElement(Route, { path: 'songs', element: createElement(songScreen.SongWorkspace) }))))));
  const view = render(tree); await waitFor(() => assert.deepEqual(calls, ['A band'])); assert.equal(aSettled, false);
  await act(async () => { navigate('/bands/band-b/songs'); }); assert.equal(view.queryByText('A 비밀 밴드'), null); await waitFor(() => assert.deepEqual(calls, ['A band', 'B band']));
  await act(async () => { b.resolve(response({ band: { id: 2, slug: 'band-b', name: 'B 공개 밴드', default_visibility: 'MEMBERS', membership: { id: 2, role: 'member' } } })); }); await view.findByText('B 선택 곡'); await userEvent.click(view.getByText('B 선택 곡')); await userEvent.click(view.getByRole('button', { name: '수정' })); await userEvent.type(view.getByLabelText('제목'), ' 초안'); assert.deepEqual(calls, ['A band', 'B band', 'B songs', 'B media']);
  await act(async () => { a.resolve(response({ band: { id: 1, slug: 'band-a', name: 'A 비밀 밴드', membership: { id: 1, role: 'member' } } })); });
  assert.equal(view.queryByText('A 비밀 밴드'), null); assert.equal(view.queryByText('A 선택 곡'), null); assert.equal(view.getByLabelText('제목').value, 'B 선택 곡 초안'); assert.deepEqual(calls, ['A band', 'B band', 'B songs', 'B media']); view.unmount(); globalThis.fetch = previousFetch;
});

test('member DOM form writes tenant-free v2 payload once; visitor sees no write control', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => { if (url.includes('/by-slug/member')) return response({ band: { id: 7, slug: 'member', name: 'Member', default_visibility: 'MEMBERS', membership: { id: 7, role: 'member' } } }); if (url.includes('/songs') && options.method === 'POST') { writes.push(JSON.parse(options.body)); return response({ song: {} }, 201); } if (url.includes('/songs?')) return response({ songs: [], pagination: { total: 0, has_more: false } }); return response({}); };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  const view = render(createElement(App, { entry: '/bands/member/songs', authValue: member, bandValue: { activeBands: [] }, songs: true }));
  await view.findByRole('button', { name: '곡 추가' }); await userEvent.click(view.getByRole('button', { name: '곡 추가' }));
  await userEvent.type(view.getByLabelText('제목'), '새 곡'); await userEvent.type(view.getByLabelText('아티스트'), '밴드');
  await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.equal(writes.length, 1));
  assert.equal('band_id' in writes[0], false); assert.equal('created_by_membership_id' in writes[0], false); assert.equal(writes[0].title, '새 곡');
  view.unmount(); globalThis.fetch = async (url) => url.includes('/by-slug/public') ? response({ band: { id: 8, slug: 'public', name: 'Public', default_visibility: 'PUBLIC', membership: null } }) : response({ songs: [], pagination: { total: 0, has_more: false } });
  const visitor = render(createElement(App, { entry: '/bands/public/songs', authValue: member, bandValue: { activeBands: [] }, songs: true })); await visitor.findByText('Public'); assert.equal(visitor.queryByRole('button', { name: '곡 추가' }), null); visitor.unmount(); globalThis.fetch = previousFetch;
});

test('rejected rapid member submissions issue one request and keep the form usable', async () => {
  await load(); const previousFetch = globalThis.fetch; const rejected = deferred(); let posts = 0;
  globalThis.fetch = async (url, options = {}) => { if (url.includes('/by-slug/reject')) return response({ band: { id: 9, slug: 'reject', name: 'Reject', default_visibility: 'MEMBERS', membership: { id: 9, role: 'member' } } }); if (url.includes('/songs') && options.method === 'POST') { posts += 1; return rejected.promise; } if (url.includes('/songs?')) return response({ songs: [], pagination: { total: 0, has_more: false } }); return response({}); };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  const view = render(createElement(App, { entry: '/bands/reject/songs', authValue: member, bandValue: { activeBands: [] }, songs: true }));
  await view.findByRole('button', { name: '곡 추가' }); await userEvent.click(view.getByRole('button', { name: '곡 추가' })); await userEvent.type(view.getByLabelText('제목'), '실패 곡'); await userEvent.type(view.getByLabelText('아티스트'), '밴드');
  const save = view.getByRole('button', { name: '저장' }); await Promise.all([userEvent.click(save), userEvent.click(save)]); await waitFor(() => assert.equal(posts, 1)); await act(async () => { rejected.resolve(response({}, 403)); }); await view.findByRole('alert');
  assert.equal(posts, 1); assert.equal(view.getByLabelText('제목').value, '실패 곡'); assert.equal(view.getByRole('button', { name: '저장' }).disabled, false);
  view.unmount(); globalThis.fetch = previousFetch;
});

test('rehearsal DOM create sends only allowed same-band fields and song ids', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = [];
  const placeRequests = [];
  globalThis.fetch = async (url, options = {}) => { if (url.includes('/by-slug/practice')) return response({ band: { id: 10, slug: 'practice', name: 'Practice', default_visibility: 'MEMBERS', membership: { id: 10, role: 'member' } } }); if (url.includes('/search-places?')) { placeRequests.push({ url, auth: options.headers.Authorization, signal: options.signal }); return response([{ title: '<script>연습실</script>', address: '서울 마포구', roadAddress: '서울 마포구 와우산로' }]); } if (url.includes('/rehearsals') && options.method === 'POST') { writes.push(JSON.parse(options.body)); return response({ rehearsal: {} }, 201); } if (url.includes('/rehearsals?')) return response({ rehearsals: [], pagination: { total: 0, has_more: false } }); if (url.includes('/songs?')) return response({ songs: [{ id: 31, title: '같은 밴드 곡', artist: '밴드' }], pagination: { total: 1, has_more: false } }); return response({}); };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  const view = render(createElement(App, { entry: '/bands/practice/rehearsals', authValue: member, bandValue: { activeBands: [] }, rehearsals: true })); await view.findByRole('button', { name: '합주 추가' }); await userEvent.click(view.getByRole('button', { name: '합주 추가' })); assert.equal(view.getByLabelText('합주 유형').value, 'REGULAR'); await userEvent.type(view.getByLabelText('제목'), '9월 합주'); await userEvent.type(view.getByLabelText('날짜'), '2026-09-27'); await userEvent.click(view.getByLabelText(/같은 밴드 곡/));
  const placeSearch = view.getByLabelText('밴드 장소 검색'); assert.equal(placeSearch.maxLength, 100); await userEvent.type(placeSearch, '홍대'); fireEvent.keyDown(placeSearch, { key: 'Enter' }); assert.equal(writes.length, 0, 'Enter in the search input does not submit the rehearsal form'); await new Promise((resolve) => setTimeout(resolve, 200)); assert.equal(placeRequests.length, 0, 'search waits for the debounce interval');
  const result = await view.findByRole('button', { name: /연습실/ }); assert.equal(placeRequests.length, 1); assert.equal(new URL(placeRequests[0].url).pathname + new URL(placeRequests[0].url).search, '/api/v2/bands/10/search-places?query=%ED%99%8D%EB%8C%80'); assert.equal(placeRequests[0].auth, 'Bearer token'); await userEvent.click(result);
  assert.equal(view.getByLabelText('장소').maxLength, 200); assert.equal(view.getByLabelText('장소').value, '<script>연습실</script> (서울 마포구 와우산로)'); await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.equal(writes.length, 1));
  assert.deepEqual(writes[0].song_ids, [31]); assert.equal(writes[0].visibility, 'MEMBERS'); assert.equal(writes[0].rehearsal_type, 'REGULAR'); assert.equal(writes[0].location, '<script>연습실</script> (서울 마포구 와우산로)'); assert.equal('latitude' in writes[0], false); assert.equal('longitude' in writes[0], false); assert.equal('band_id' in writes[0], false); assert.equal('created_by_membership_id' in writes[0], false); view.unmount(); globalThis.fetch = previousFetch;
});

test('the v2 rehearsal type draft resets when changing bands or accounts', async () => {
  await load(); const previousFetch = globalThis.fetch; let navigate;
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/')) { const slug = String(url).split('/').at(-1); return response({ band: { id: slug === 'type-a' ? 101 : 102, slug, name: slug.toUpperCase(), default_visibility: 'MEMBERS', membership: { id: slug === 'type-a' ? 101 : 102, role: 'member' } } }); }
    if (url.includes('/rehearsals?')) return response({ rehearsals: [], pagination: { has_more: false } });
    if (url.includes('/songs?')) return response({ songs: [] });
    return response({});
  };
  const Go = () => { navigate = useNavigate(); return null; };
  const tree = (authValue) => createElement(
    policies.ServicePolicyContext.Provider, { value: { config: { policies: { upload: { enabled: false } } } } },
    createElement(auth.AuthContext.Provider, { value: authValue },
      createElement(bands.BandContext.Provider, { value: { activeBands: [] } },
        createElement(MemoryRouter, { initialEntries: ['/bands/type-a/rehearsals'] },
          createElement(Go),
          createElement(Routes, null,
            createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) },
              createElement(Route, { path: 'rehearsals', element: createElement(rehearsalScreen.RehearsalWorkspace) }),
            ),
          ),
        ),
      ),
    ),
  );
  const member = (id) => ({ user: { id }, getAccessToken: async () => id, refreshSession: async () => id, signOut: async () => {} });
  try {
    const view = render(tree(member('account-a'))); await view.findByText('TYPE-A'); await userEvent.click(view.getByRole('button', { name: '합주 추가' })); fireEvent.change(view.getByLabelText('합주 유형'), { target: { value: 'LIVE' } });
    await act(async () => navigate('/bands/type-b/rehearsals')); await view.findByText('TYPE-B'); await userEvent.click(view.getByRole('button', { name: '합주 추가' })); assert.equal(view.getByLabelText('합주 유형').value, 'REGULAR'); fireEvent.change(view.getByLabelText('합주 유형'), { target: { value: 'LIVE' } });
    view.rerender(tree(member('account-b'))); await view.findByText('TYPE-B'); assert.equal(view.queryByLabelText('합주 유형'), null); await userEvent.click(view.getByRole('button', { name: '합주 추가' })); assert.equal(view.getByLabelText('합주 유형').value, 'REGULAR');
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('the v2 rehearsal form sends a member-selected LIVE type on create', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/live-create')) return response({ band: { id: 104, slug: 'live-create', name: '공연 밴드', membership: { id: 104, role: 'member' } } });
    if (String(url).includes('/rehearsals') && options.method === 'POST') { writes.push(JSON.parse(options.body)); return response({ rehearsal: {} }, 201); }
    if (String(url).includes('/rehearsals?')) return response({ rehearsals: [], pagination: { has_more: false } });
    if (String(url).includes('/songs?')) return response({ songs: [] });
    return response({});
  };
  const member = { user: { id: 'live-member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  try {
    const view = render(createElement(App, { entry: '/bands/live-create/rehearsals', authValue: member, bandValue: { activeBands: [] }, rehearsals: true })); await view.findByRole('button', { name: '합주 추가' }); await userEvent.click(view.getByRole('button', { name: '합주 추가' })); fireEvent.change(view.getByLabelText('합주 유형'), { target: { value: 'LIVE' } }); await userEvent.type(view.getByLabelText('제목'), '라이브 공연'); await userEvent.type(view.getByLabelText('날짜'), '2026-11-20'); await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.equal(writes.length, 1)); assert.equal(writes[0].rehearsal_type, 'LIVE'); assert.equal(writes[0].title, '라이브 공연'); view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('place search treats permission denial as terminal, hides stale results, and keeps manual entry usable', async () => {
  await load(); const previousFetch = globalThis.fetch; const requests = []; let searchCount = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/place-denied')) return response({ band: { id: 11, slug: 'place-denied', name: '장소 밴드', membership: { id: 11, role: 'member' } } });
    if (url.includes('/search-places?')) { requests.push(String(url)); searchCount += 1; return searchCount === 1 ? response([{ title: '이전 결과', address: '서울' }]) : response({ message: 'hidden' }, 403); }
    if (url.includes('/rehearsals') && options.method === 'POST') return response({ rehearsal: {} }, 201);
    if (url.includes('/rehearsals?')) return response({ rehearsals: [], pagination: { total: 0, has_more: false } });
    if (url.includes('/songs?')) return response({ songs: [], pagination: { total: 0, has_more: false } });
    return response({});
  };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  try {
    const view = render(createElement(App, { entry: '/bands/place-denied/rehearsals', authValue: member, bandValue: { activeBands: [] }, rehearsals: true }));
    await view.findByRole('button', { name: '합주 추가' }); await userEvent.click(view.getByRole('button', { name: '합주 추가' }));
    const search = view.getByLabelText('밴드 장소 검색'); await userEvent.type(search, '첫 검색'); await view.findByRole('button', { name: /이전 결과/ });
    fireEvent.change(search, { target: { value: '' } }); await userEvent.type(search, '권한 없음'); await view.findByText(/밴드 멤버만 장소를 검색할 수 있습니다/);
    assert.equal(view.queryByRole('button', { name: /이전 결과/ }), null);
    fireEvent.change(search, { target: { value: '' } }); await userEvent.type(search, '추가 시도'); await new Promise((resolve) => setTimeout(resolve, 420)); assert.equal(requests.length, 2, '403 disables search for this workspace');
    await userEvent.type(view.getByLabelText('장소'), '직접 입력 장소'); assert.equal(view.getByLabelText('장소').value, '직접 입력 장소');
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('place provider failures preserve manual rehearsal location entry', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/place-provider-error')) return response({ band: { id: 12, slug: 'place-provider-error', name: '검색 장애 밴드', membership: { id: 12, role: 'member' } } });
    if (url.includes('/search-places?')) return response({ message: 'provider unavailable' }, 503);
    if (url.includes('/rehearsals?')) return response({ rehearsals: [], pagination: { total: 0, has_more: false } });
    if (url.includes('/songs?')) return response({ songs: [], pagination: { total: 0, has_more: false } });
    return response({});
  };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  try {
    const view = render(createElement(App, { entry: '/bands/place-provider-error/rehearsals', authValue: member, bandValue: { activeBands: [] }, rehearsals: true }));
    await view.findByRole('button', { name: '합주 추가' }); await userEvent.click(view.getByRole('button', { name: '합주 추가' }));
    await userEvent.type(view.getByLabelText('밴드 장소 검색'), '장소 오류'); await view.findByText(/장소 검색을 사용할 수 없습니다/);
    await userEvent.type(view.getByLabelText('장소'), '수동 입력 연습실'); assert.equal(view.getByLabelText('장소').value, '수동 입력 연습실'); view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('place search rate limits without retrying automatically and late requests cannot cross account or unmount boundaries', async () => {
  await load(); const previousFetch = globalThis.fetch; const lateSearch = deferred(); const unmountSearch = deferred(); const calls = []; let phase = 'rate-limit';
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url); const method = options.method || 'GET'; const token = options.headers?.Authorization;
    if (value.includes('/by-slug/place-race')) return response({ band: { id: token === 'Bearer user-b' ? 22 : 21, slug: 'place-race', name: token === 'Bearer user-b' ? 'B 장소 밴드' : 'A 장소 밴드', membership: { id: token === 'Bearer user-b' ? 22 : 21, role: 'member' } } });
    if (value.includes('/search-places?')) { calls.push({ url: value, signal: options.signal, token }); if (phase === 'rate-limit') return response({}, 429); if (token === 'Bearer user-a') return lateSearch.promise; if (new URL(value).searchParams.get('query') === '언마운트') return unmountSearch.promise; return response([{ title: 'B 장소', address: '서울 B구' }]); }
    if (value.includes('/rehearsals') && method === 'POST') return response({ rehearsal: {} }, 201);
    if (value.includes('/rehearsals?')) return response({ rehearsals: [], pagination: { total: 0, has_more: false } });
    if (value.includes('/songs?')) return response({ songs: [], pagination: { total: 0, has_more: false } });
    return response({});
  };
  const account = (id) => ({ user: { id }, getAccessToken: async () => id, refreshSession: async () => id, signOut: async () => {} });
  try {
    const view = render(createElement(App, { entry: '/bands/place-race/rehearsals', authValue: account('user-a'), bandValue: { activeBands: [] }, rehearsals: true }));
    await view.findByRole('button', { name: '합주 추가' }); await userEvent.click(view.getByRole('button', { name: '합주 추가' }));
    await userEvent.type(view.getByLabelText('밴드 장소 검색'), '요청 제한'); await view.findByText(/검색 요청이 많습니다/); assert.equal(calls.length, 1);
    const locationSearch = view.getByLabelText('밴드 장소 검색'); fireEvent.change(locationSearch, { target: { value: '' } }); await userEvent.type(locationSearch, '다른 검색'); await new Promise((resolve) => setTimeout(resolve, 420)); assert.equal(calls.length, 1, 'cooldown blocks additional API traffic');
    await userEvent.click(view.getByRole('button', { name: '취소' }));
    await userEvent.click(view.getByRole('button', { name: '합주 추가' }));
    phase = 'deferred'; await userEvent.type(view.getByLabelText('밴드 장소 검색'), '이전 계정'); await waitFor(() => assert.equal(calls.length, 2));
    view.rerender(createElement(App, { entry: '/bands/place-race/rehearsals', authValue: account('user-b'), bandValue: { activeBands: [] }, rehearsals: true }));
    await view.findByText('B 장소 밴드'); assert.equal(calls[1].signal.aborted, true);
    lateSearch.resolve(response([{ title: 'A 계정 장소', address: '이전 밴드' }])); await new Promise((resolve) => setTimeout(resolve, 0)); assert.equal(view.queryByText('A 계정 장소'), null);
    await userEvent.click(view.getByRole('button', { name: '합주 추가' })); await userEvent.type(view.getByLabelText('밴드 장소 검색'), '새 계정'); await view.findByRole('button', { name: /B 장소.*서울 B구/ }); assert.equal(calls.at(-1).token, 'Bearer user-b');
    fireEvent.change(view.getByLabelText('밴드 장소 검색'), { target: { value: '' } }); await userEvent.type(view.getByLabelText('밴드 장소 검색'), '언마운트'); await waitFor(() => assert.equal(calls.length, 4));
    const pendingSignal = calls.at(-1).signal; await userEvent.click(view.getByRole('button', { name: '취소' })); assert.equal(pendingSignal.aborted, true); unmountSearch.resolve(response([{ title: '닫힌 폼', address: '서울' }])); assert.equal(view.queryByLabelText('밴드 장소 검색'), null); view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('account change remounts the workspace and clears the prior form, media, and paged list', async () => {
  await load(); const previousFetch = globalThis.fetch;
  const account = (id) => ({ user: { id }, getAccessToken: async () => id, refreshSession: async () => id, signOut: async () => {} });
  globalThis.fetch = async (url, options = {}) => {
    const a = options.headers?.Authorization === 'Bearer account-a';
    if (url.includes('/by-slug/shared')) return response({ band: { id: a ? 40 : 41, slug: 'shared', name: a ? 'A 밴드' : 'B 밴드', default_visibility: 'MEMBERS', membership: { id: a ? 40 : 41, role: 'member' } } });
    if (url.includes('/songs/401/media?')) return response({ media: [{ id: 501, file_type: 'video', playback: { video_url: 'https://media.invalid/a.mp4' } }], pagination: { has_more: false } });
    if (url.includes('/songs?')) {
      const second = url.includes('offset=20');
      return response({ songs: a ? (second ? [{ id: 402, title: 'A 다음 페이지', artist: 'A' }] : [{ id: 401, title: 'A 선택 곡', artist: 'A', can_edit: true }]) : [{ id: 403, title: 'B 새 곡', artist: 'B', can_edit: true }], pagination: { has_more: a && !second, next_offset: 20 } });
    }
    return response({});
  };
  const view = render(createElement(App, { entry: '/bands/shared/songs', authValue: account('account-a'), bandValue: { activeBands: [] }, songs: true }));
  await view.findByText('A 선택 곡'); await userEvent.click(view.getByRole('button', { name: '더 불러오기' })); await view.findByText('A 다음 페이지'); await userEvent.click(view.getByText('A 선택 곡'));
  await view.findByText('영상 #501'); await userEvent.click(view.getByRole('button', { name: '수정' })); assert.equal(view.getByLabelText('제목').value, 'A 선택 곡');
  view.rerender(createElement(App, { entry: '/bands/shared/songs', authValue: account('account-b'), bandValue: { activeBands: [] }, songs: true }));
  assert.equal(view.queryByDisplayValue('A 선택 곡'), null); assert.equal(view.queryByText('A 다음 페이지'), null); assert.equal(view.queryByText('영상 #501'), null);
  await view.findByText('B 새 곡'); assert.equal(view.queryByText('A 선택 곡'), null); view.unmount(); globalThis.fetch = previousFetch;
});

test('direct song entry aborts a pending save when the account changes', async () => {
  await load(); const previousFetch = globalThis.fetch; const pending = deferred(); let saveSignal; let oldListReads = 0;
  const account = (id) => ({ user: { id }, getAccessToken: async () => id, refreshSession: async () => id, signOut: async () => {} });
  globalThis.fetch = async (url, options = {}) => {
    const oldAccount = options.headers?.Authorization === 'Bearer account-a';
    if (url.includes('/by-slug/save-race')) return response({ band: { id: oldAccount ? 141 : 142, slug: 'save-race', name: oldAccount ? 'A 밴드' : 'B 밴드', default_visibility: 'MEMBERS', membership: { id: oldAccount ? 141 : 142, role: 'member' } } });
    if (url.includes('/bands/141/songs') && options.method === 'POST') { saveSignal = options.signal; return pending.promise; }
    if (url.includes('/bands/141/songs?')) { oldListReads += 1; return response({ songs: [], pagination: { has_more: false } }); }
    if (url.includes('/bands/142/songs?')) return response({ songs: [{ id: 1421, title: 'B 전용 곡', artist: 'B' }], pagination: { has_more: false } });
    throw new Error(`unexpected request ${url}`);
  };
  try {
    const view = render(createElement(App, { entry: '/bands/save-race/songs', authValue: account('account-a'), bandValue: { activeBands: [] }, songs: true }));
    await view.findByRole('button', { name: '곡 추가' }); await userEvent.click(view.getByRole('button', { name: '곡 추가' }));
    await userEvent.type(view.getByLabelText('제목'), 'A 초안'); await userEvent.type(view.getByLabelText('아티스트'), 'A');
    await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.ok(saveSignal));
    view.rerender(createElement(App, { entry: '/bands/save-race/songs', authValue: account('account-b'), bandValue: { activeBands: [] }, songs: true }));
    await view.findByText('B 전용 곡'); assert.equal(saveSignal.aborted, true); assert.equal(view.queryByDisplayValue('A 초안'), null);
    await act(async () => { pending.resolve(response({ song: { id: 1411, title: 'A 초안' } }, 201)); });
    assert.equal(view.queryByText('A 초안'), null); assert.equal(view.getByText('B 전용 곡').textContent, 'B 전용 곡'); assert.equal(oldListReads, 1);
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('song media uses type-safe playback fallbacks and stable display names', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/playback')) return response({ band: { id: 70, slug: 'playback', name: '재생 밴드', membership: null } });
    if (url.includes('/songs/71/media?')) return response({ media: [
      { id: 1, file_type: 'audio', display_name: 'practice take.m4a', playback: { url: 'https://media.invalid/original.m4a' } },
      { id: 2, file_type: 'video', display_name: 'band rehearsal.mp4', playback: { video_url: 'https://media.invalid/video.mp4', audio_url: 'https://media.invalid/audio.m4a' } },
      { id: 3, file_type: 'video', playback: { url: 'https://media.invalid/video-only.mp4' } },
      { id: 4, file_type: 'video', playback: { audio_url: 'https://media.invalid/extracted.m4a' } },
      { id: 5, file_type: 'audio', playback: {} },
    ], pagination: { has_more: false } });
    if (url.includes('/songs?')) return response({ songs: [{ id: 71, title: '재생 확인 곡', artist: '밴드' }], pagination: { has_more: false } });
    return response({});
  };
  try {
    const visitor = { user: null, getAccessToken: async () => null, refreshSession: async () => null, signOut: async () => {} };
    const view = render(createElement(App, { entry: '/bands/playback/songs', authValue: visitor, bandValue: { activeBands: [] }, songs: true }));
    await userEvent.click(await view.findByText('재생 확인 곡'));
    const named = (name) => [...view.container.querySelectorAll('.workspace-media')].find((section) => section.querySelector('strong')?.textContent === name);
    const originalAudio = await waitFor(() => { const item = named('practice take.m4a')?.querySelector('audio'); assert.ok(item); return item; });
    assert.equal(originalAudio.getAttribute('src'), 'https://media.invalid/original.m4a');
    const videoAndAudio = named('band rehearsal.mp4');
    assert.equal(videoAndAudio.querySelector('video')?.getAttribute('src'), 'https://media.invalid/video.mp4');
    await userEvent.click(view.getByRole('button', { name: '음원만 듣기' }));
    assert.equal(videoAndAudio.querySelector('audio')?.getAttribute('src'), 'https://media.invalid/audio.m4a');
    const videoOnly = named('영상 #3');
    assert.equal(videoOnly.querySelector('video')?.getAttribute('src'), 'https://media.invalid/video-only.mp4');
    assert.equal(videoOnly.querySelector('audio'), null);
    const extractedAudio = named('영상 #4');
    assert.equal(extractedAudio.querySelector('audio')?.getAttribute('src'), 'https://media.invalid/extracted.m4a');
    assert.equal(named('음원 #5').textContent.includes('재생 가능한 파일이 없습니다.'), true);
    assert.equal(view.queryByText(/720p/), null);
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('song edit and confirmed delete issue exact scoped requests and refetch the rendered list', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = []; let songs = [{ id: 61, title: '원래 곡', artist: '원래 아티스트', status: 'Practice', visibility: 'MEMBERS', can_edit: true, can_delete: true }];
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/manage')) return response({ band: { id: 60, slug: 'manage', name: '관리 밴드', default_visibility: 'MEMBERS', membership: { id: 60, role: 'member' } } });
    if (url.includes('/songs/61/media?')) return response({ media: [], pagination: { has_more: false } });
    if (url.endsWith('/songs/61') && options.method === 'PATCH') { writes.push({ url, body: JSON.parse(options.body) }); songs = [{ ...songs[0], title: '수정된 곡', artist: '수정 아티스트' }]; return response({ song: songs[0] }); }
    if (url.endsWith('/songs/61') && options.method === 'DELETE') { writes.push({ url, method: 'DELETE' }); songs = []; return response({}); }
    if (url.includes('/songs?')) return response({ songs, pagination: { total: songs.length, has_more: false } });
    return response({});
  };
  const member = { user: { id: 'editor' }, getAccessToken: async () => 'editor', refreshSession: async () => 'editor', signOut: async () => {} }; const previousConfirm = window.confirm; window.confirm = () => true;
  try {
    const view = render(createElement(App, { entry: '/bands/manage/songs', authValue: member, bandValue: { activeBands: [] }, songs: true })); await view.findByText('원래 곡'); await userEvent.click(view.getByText('원래 곡')); await userEvent.click(view.getByRole('button', { name: '수정' }));
    await userEvent.type(view.getByLabelText('제목'), ' 업데이트'); await userEvent.type(view.getByLabelText('아티스트'), ' 업데이트'); await userEvent.click(view.getByRole('button', { name: '저장' })); await view.findByText('수정된 곡');
    assert.equal(writes[0].url, 'http://localhost:5000/api/v2/bands/60/songs/61'); assert.deepEqual(writes[0].body, { title: '원래 곡 업데이트', artist: '원래 아티스트 업데이트', status: 'Practice', chords: '', link: '', memo: '', genre: '', visibility: 'MEMBERS' });
    assert.equal('band_id' in writes[0].body, false); assert.equal('created_by_membership_id' in writes[0].body, false);
    await userEvent.click(view.getByText('수정된 곡')); await userEvent.click(view.getByRole('button', { name: '삭제' })); await waitFor(() => assert.equal(writes.filter((write) => write.method === 'DELETE').length, 1)); await view.findByText('표시할 곡이 없습니다.'); assert.equal(writes.at(-1).url.endsWith('/api/v2/bands/60/songs/61'), true); view.unmount();
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('rejected PATCH remains retryable while delete permission denial disables the stale action', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/reject-edit')) return response({ band: { id: 62, slug: 'reject-edit', name: '실패 밴드', default_visibility: 'MEMBERS', membership: { id: 62, role: 'member' } } });
    if (url.includes('/songs/63/media?')) return response({ media: [], pagination: { has_more: false } });
    if (url.endsWith('/songs/63') && (options.method === 'PATCH' || options.method === 'DELETE')) return response({}, 403);
    if (url.includes('/songs?')) return response({ songs: [{ id: 63, title: '실패 곡', artist: '밴드', can_edit: true, can_delete: true, visibility: 'MEMBERS' }], pagination: { has_more: false } });
    return response({});
  };
  const member = { user: { id: 'editor' }, getAccessToken: async () => 'editor', refreshSession: async () => 'editor', signOut: async () => {} }; const previousConfirm = window.confirm; window.confirm = () => true;
  try {
    const view = render(createElement(App, { entry: '/bands/reject-edit/songs', authValue: member, bandValue: { activeBands: [] }, songs: true })); await view.findByText('실패 곡'); await userEvent.click(view.getByText('실패 곡')); await userEvent.click(view.getByRole('button', { name: '수정' })); await userEvent.click(view.getByRole('button', { name: '저장' })); await view.findByRole('alert'); assert.equal(view.getByRole('button', { name: '저장' }).disabled, false); assert.equal(view.getByLabelText('제목').value, '실패 곡');
    await userEvent.click(view.getByRole('button', { name: '취소' })); await userEvent.click(view.getByRole('button', { name: '삭제' })); await view.findByText('삭제 권한이 없습니다. 밴드 권한을 새로 확인해 주세요.'); assert.equal(view.getByRole('button', { name: '권한 확인 필요' }).disabled, true); await userEvent.click(view.getByRole('button', { name: '수정' })); await view.findByRole('button', { name: '저장' }); view.unmount();
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('rehearsal edit uses the current band song choices and an identity-free PATCH payload', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = []; let currentType;
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/edit-practice')) return response({ band: { id: 70, slug: 'edit-practice', name: '합주 밴드', default_visibility: 'MEMBERS', membership: { id: 70, role: 'member' } } });
    if (url.endsWith('/rehearsals/71') && options.method === 'PATCH') { const body = JSON.parse(options.body); writes.push(body); if ('rehearsal_type' in body) currentType = body.rehearsal_type; return response({ rehearsal: {} }); }
    if (url.includes('/rehearsals?')) return response({ rehearsals: [{ id: 71, title: '이전 합주', date: '2026-09-20', visibility: 'PUBLIC', can_edit: true, ...(currentType ? { rehearsal_type: currentType } : {}), songs: [{ id: 72, title: '이전 밴드 곡' }] }], pagination: { has_more: false } });
    if (url.includes('/songs?')) return response({ songs: [{ id: 72, title: '이전 밴드 곡', artist: '밴드' }, { id: 73, title: '현재 밴드 곡', artist: '밴드' }], pagination: { has_more: false } });
    if (url.includes('/rehearsals/71/media?')) return response({ media: [], pagination: { has_more: false } });
    return response({});
  };
  const member = { user: { id: 'editor' }, getAccessToken: async () => 'editor', refreshSession: async () => 'editor', signOut: async () => {} }; const view = render(createElement(App, { entry: '/bands/edit-practice/rehearsals', authValue: member, bandValue: { activeBands: [] }, rehearsals: true }));
  await view.findByText('이전 합주'); assert.equal(view.getByText('미분류').dataset.rehearsalType, 'UNCLASSIFIED'); await userEvent.click(view.getByText('이전 합주')); assert.equal((await view.findAllByText('미분류', { selector: '.rehearsal-type-badge' })).length, 2); await userEvent.click(view.getByRole('button', { name: '수정' })); await view.findByLabelText(/현재 밴드 곡/); assert.equal(view.getByLabelText('합주 유형').value, 'UNCLASSIFIED'); await userEvent.click(view.getByLabelText(/이전 밴드 곡/)); await userEvent.click(view.getByLabelText(/현재 밴드 곡/)); await userEvent.type(view.getByLabelText('제목'), ' 수정'); await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.equal(writes.length, 1));
  assert.deepEqual(writes[0], { title: '이전 합주 수정', date: '2026-09-20', start_date: null, end_date: null, time: '', location: '', memo: '', color: '', visibility: 'PUBLIC', song_ids: [73] }); assert.equal('rehearsal_type' in writes[0], false); assert.equal('band_id' in writes[0], false); assert.equal('created_by_membership_id' in writes[0], false);
  await view.findByText('이전 합주'); await userEvent.click(view.getByText('이전 합주')); await userEvent.click(view.getByRole('button', { name: '수정' })); fireEvent.change(view.getByLabelText('합주 유형'), { target: { value: 'LIVE' } }); await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.equal(writes.length, 2)); assert.equal(writes[1].rehearsal_type, 'LIVE');
  await waitFor(() => assert.equal(view.getByText('라이브공연', { selector: '.rehearsal-type-badge' }).dataset.rehearsalType, 'LIVE')); await userEvent.click(view.getByText('이전 합주')); await userEvent.click(view.getByRole('button', { name: '수정' })); assert.equal(view.getByLabelText('합주 유형').value, 'LIVE'); await userEvent.type(view.getByLabelText('제목'), ' 유지'); await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.equal(writes.length, 3)); assert.equal('rehearsal_type' in writes[2], false);
  await userEvent.click(view.getByText('이전 합주')); await userEvent.click(view.getByRole('button', { name: '수정' })); assert.equal(view.getByLabelText('합주 유형').value, 'LIVE'); await userEvent.type(view.getByLabelText('제목'), ' 초안'); await userEvent.click(view.getByRole('button', { name: '합주 추가' })); assert.equal(view.getByLabelText('합주 유형').value, 'REGULAR'); assert.equal(view.getByLabelText('제목').value, ''); assert.equal(view.getByLabelText('날짜').value, '');
  view.unmount(); globalThis.fetch = previousFetch;
});

test('media pagination renders the next offset without duplicate media and bounds each playback refresh', async () => {
  await load(); const previousFetch = globalThis.fetch; const mediaUrls = [];
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/media')) return response({ band: { id: 80, slug: 'media', name: '미디어 밴드', default_visibility: 'MEMBERS', membership: { id: 80, role: 'member' } } });
    if (url.includes('/songs/81/media?')) { mediaUrls.push(url); return response(url.includes('offset=20') ? { media: [{ id: 811, file_type: 'video', playback: { video_url: 'https://media.invalid/video.mp4' } }, { id: 813, file_type: 'video', playback: { video_url: 'https://media.invalid/next.mp4' } }], pagination: { has_more: false } } : { media: [{ id: 811, file_type: 'video', playback: { video_url: 'https://media.invalid/video.mp4' } }, { id: 812, file_type: 'audio', playback: { audio_url: 'https://media.invalid/audio.m4a' } }], pagination: { has_more: true, next_offset: 20 } }); }
    if (url.includes('/songs?')) return response({ songs: [{ id: 81, title: '미디어 곡', artist: '밴드', can_edit: true }], pagination: { has_more: false } });
    return response({});
  };
  const member = { user: { id: 'media' }, getAccessToken: async () => 'media', refreshSession: async () => 'media', signOut: async () => {} }; const view = render(createElement(App, { entry: '/bands/media/songs', authValue: member, bandValue: { activeBands: [] }, songs: true }));
  await view.findByText('미디어 곡'); await userEvent.click(view.getByText('미디어 곡')); await waitFor(() => assert.equal(view.container.querySelectorAll('.workspace-media').length, 2)); const video = view.container.querySelector('video'); const audio = view.container.querySelector('audio'); assert.ok(video); assert.ok(audio);
  fireEvent.error(video); fireEvent.error(video); fireEvent.error(audio); fireEvent.error(audio); await waitFor(() => assert.equal(mediaUrls.filter((url) => url.includes('offset=0')).length, 3));
  await userEvent.click(view.getByRole('button', { name: '미디어 더 불러오기' })); await waitFor(() => assert.equal(mediaUrls.some((url) => url.endsWith('/api/v2/bands/80/songs/81/media?limit=20&offset=20')), true)); await waitFor(() => assert.equal(view.container.querySelectorAll('.workspace-media').length, 3)); view.unmount(); globalThis.fetch = previousFetch;
});

test('a stale playback refresh cannot repaint media after navigating to another band', async () => {
  await load(); const previousFetch = globalThis.fetch; const oldRefresh = deferred(); let mediaCalls = 0; let navigate;
  globalThis.fetch = async (url) => {
    if (url.includes('/by-slug/old-media')) return response({ band: { id: 90, slug: 'old-media', name: '이전 밴드', default_visibility: 'MEMBERS', membership: { id: 90, role: 'member' } } });
    if (url.includes('/by-slug/new-media')) return response({ band: { id: 91, slug: 'new-media', name: '새 밴드', default_visibility: 'MEMBERS', membership: { id: 91, role: 'member' } } });
    if (url.includes('/songs/901/media?')) { mediaCalls += 1; return mediaCalls === 1 ? response({ media: [{ id: 902, file_type: 'video', playback: { video_url: 'https://media.invalid/old.mp4' } }], pagination: { has_more: false } }) : oldRefresh.promise; }
    if (url.includes('/songs?')) return response({ songs: url.includes('/bands/90/') ? [{ id: 901, title: '이전 미디어', artist: 'A' }] : [{ id: 903, title: '새 미디어', artist: 'B' }], pagination: { has_more: false } });
    return response({});
  };
  const Go = () => { navigate = useNavigate(); return null; }; const member = { user: { id: 'media' }, getAccessToken: async () => 'media', refreshSession: async () => 'media', signOut: async () => {} };
  const tree = createElement(auth.AuthContext.Provider, { value: member },
    createElement(bands.BandContext.Provider, { value: { activeBands: [] } },
      createElement(MemoryRouter, { initialEntries: ['/bands/old-media/songs'] }, createElement(Go),
        createElement(Routes, null,
          createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) },
            createElement(Route, { path: 'songs', element: createElement(songScreen.SongWorkspace) }))))));
  const view = render(tree);
  await view.findByText('이전 미디어'); await userEvent.click(view.getByText('이전 미디어')); await waitFor(() => assert.ok(view.container.querySelector('video'))); fireEvent.error(view.container.querySelector('video')); await waitFor(() => assert.equal(mediaCalls, 2)); await act(async () => { navigate('/bands/new-media/songs'); }); await view.findByText('새 미디어'); await act(async () => { oldRefresh.resolve(response({ media: [{ id: 902, file_type: 'video', playback: { video_url: 'https://media.invalid/old-refreshed.mp4' } }], pagination: { has_more: false } })); });
  assert.equal(view.queryByText('이전 미디어'), null); assert.equal(view.container.querySelector('video'), null); view.unmount(); globalThis.fetch = previousFetch;
});

test('member media engagement uses v2 account payloads, refetches score order, and manages flat comments', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = []; let mediaReads = 0; let votePuts = 0; const delayedVote = deferred();
  const root = { id: 110, author: '멤버', content: '첫 댓글', parent_id: null, created_at: '2026-09-01T00:00:00Z', can_edit: true, can_delete: true };
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/engagement')) return response({ band: { id: 100, slug: 'engagement', name: '참여 밴드', default_visibility: 'PUBLIC', membership: { id: 10, role: 'member', status: 'ACTIVE' } } });
    if (url.includes('/songs?')) return response({ songs: [{ id: 101, title: '참여 곡', artist: '밴드' }], pagination: { has_more: false } });
    if (url.includes('/songs/101/media?')) { mediaReads += 1; return response({ media: [{ id: 102, file_type: 'audio', vote_score: mediaReads > 1 ? 1 : 0, viewer_vote: mediaReads > 1 ? 1 : -1, playback: { audio_url: 'https://media.invalid/clip.m4a' } }], pagination: { has_more: false } }); }
    if (url.includes('/media/102/vote') && options.method === 'PUT') { votePuts += 1; writes.push({ kind: 'vote', body: JSON.parse(options.body) }); return delayedVote.promise; }
    if (url.includes('/media/102/comments?')) return response(url.includes('offset=20') ? { comments: [{ ...root }, { id: 111, author: '멤버', content: '답글', parent_id: 110, created_at: '2026-09-02T00:00:00Z' }, { id: 112, author: '멤버', content: '고아', parent_id: 999, created_at: '2026-09-03T00:00:00Z' }, { id: 113, author: '멤버', content: '순환 A', parent_id: 114, created_at: '2026-09-04T00:00:00Z' }, { id: 114, author: '멤버', content: '순환 B', parent_id: 113, created_at: '2026-09-05T00:00:00Z' }], pagination: { has_more: false } } : { comments: [root], pagination: { has_more: true, next_offset: 20 } });
    if (url.includes('/media/102/comments') && options.method === 'POST') { writes.push({ kind: 'create', body: JSON.parse(options.body) }); return response({ comment: { id: 111, author: '멤버', content: '답글', parent_id: 110, created_at: '2026-09-02T00:00:00Z' } }, 201); }
    if (url.includes('/comments/110') && options.method === 'PATCH') { writes.push({ kind: 'edit', body: JSON.parse(options.body) }); return response({ comment: { ...root, content: '수정 댓글' } }); }
    if (url.includes('/comments/110') && options.method === 'DELETE') { writes.push({ kind: 'delete' }); return response({ deleted: true, id: 110 }); }
    return response({});
  };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} }; const confirm = window.confirm; window.confirm = () => true;
  try {
    const view = render(createElement(App, { entry: '/bands/engagement/songs', authValue: member, bandValue: { activeBands: [] }, songs: true }));
    await view.findByText('참여 곡'); await userEvent.click(view.getByText('참여 곡')); await view.findByRole('button', { name: '추천' }); assert.equal(view.getByRole('button', { name: '아쉬워요' }).getAttribute('aria-pressed'), 'true');
    fireEvent.click(view.getByRole('button', { name: '추천' })); fireEvent.click(view.getByRole('button', { name: '추천' })); await waitFor(() => assert.equal(votePuts, 1)); assert.deepEqual(writes[0], { kind: 'vote', body: { value: 1 } }); await act(async () => { delayedVote.resolve(response({ media: { id: 102, vote_score: 1, viewer_vote: 1 }, viewer_vote: 1 })); }); await waitFor(() => assert.ok(mediaReads >= 2)); assert.equal(view.getByRole('button', { name: '추천' }).getAttribute('aria-pressed'), 'true');
    await userEvent.click(view.getByRole('button', { name: '답글' })); await userEvent.type(view.getByLabelText('댓글 내용'), '답글'); await waitFor(() => assert.equal(view.getByRole('button', { name: '댓글 등록' }).disabled, false)); await userEvent.click(view.getByRole('button', { name: '댓글 등록' })); await waitFor(() => assert.deepEqual(writes.find((item) => item.kind === 'create'), { kind: 'create', body: { content: '답글', parent_id: 110 } }));
    await userEvent.click(view.getByRole('button', { name: '수정' })); await userEvent.type(view.getByLabelText('댓글 수정'), ' 수정'); await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.deepEqual(writes.find((item) => item.kind === 'edit'), { kind: 'edit', body: { content: '첫 댓글 수정' } }));
    await userEvent.click(view.getByRole('button', { name: '댓글 더 불러오기' })); await waitFor(() => assert.equal(view.container.querySelectorAll('[data-comment-id="111"]').length, 1)); assert.equal(view.container.querySelectorAll('[data-comment-id="112"],[data-comment-id="113"],[data-comment-id="114"]').length, 3); await userEvent.click(view.getByRole('button', { name: '삭제' })); await waitFor(() => assert.equal(writes.some((item) => item.kind === 'delete'), true)); view.unmount();
  } finally { window.confirm = confirm; globalThis.fetch = previousFetch; }
});

test('stale comment reads and writes cannot repaint a newly selected band through the UI router', async () => {
  await load(); const previousFetch = globalThis.fetch; const oldMore = deferred(); const oldPost = deferred(); let oldCommentReads = 0; let oldPosts = 0; let newCommentReads = 0;
  const oldComment = { id: 1003, author: '이전 멤버', content: '이전 댓글', parent_id: null, created_at: '2026-09-05T00:00:00Z' };
  const newComment = { id: 2003, author: '새 멤버', content: '새 댓글', parent_id: null, created_at: '2026-09-06T00:00:00Z' };
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/old-comments')) return response({ band: { id: 100, slug: 'old-comments', name: '이전 댓글 밴드', default_visibility: 'MEMBERS', membership: { id: 100, role: 'member' } } });
    if (url.includes('/by-slug/new-comments')) return response({ band: { id: 200, slug: 'new-comments', name: '새 댓글 밴드', default_visibility: 'MEMBERS', membership: { id: 200, role: 'member' } } });
    if (url.includes('/bands/100/songs?')) return response({ songs: [{ id: 1001, title: '이전 댓글 곡', artist: 'A' }], pagination: { has_more: false } });
    if (url.includes('/bands/200/songs?')) return response({ songs: [{ id: 2001, title: '새 댓글 곡', artist: 'B' }], pagination: { has_more: false } });
    if (url.includes('/songs/1001/media?')) return response({ media: [{ id: 1002, file_type: 'audio', playback: { audio_url: 'https://media.invalid/old-comments.m4a' } }], pagination: { has_more: false } });
    if (url.includes('/songs/2001/media?')) return response({ media: [{ id: 2002, file_type: 'audio', playback: { audio_url: 'https://media.invalid/new-comments.m4a' } }], pagination: { has_more: false } });
    if (url.includes('/media/1002/comments?')) { oldCommentReads += 1; return oldCommentReads === 1 ? response({ comments: [oldComment], pagination: { has_more: true, next_offset: 20 } }) : oldMore.promise; }
    if (url.includes('/media/2002/comments?')) { newCommentReads += 1; return response({ comments: [newComment], pagination: { has_more: false } }); }
    if (url.includes('/media/1002/comments') && options.method === 'POST') { oldPosts += 1; return oldPost.promise; }
    throw new Error(`unexpected request ${url}`);
  };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  try {
    const view = render(createElement(App, { entry: '/bands/old-comments/songs', authValue: member, bandValue: { activeBands: [{ id: 100, slug: 'old-comments', name: '이전 댓글 밴드' }, { id: 200, slug: 'new-comments', name: '새 댓글 밴드' }] }, songs: true }));
    await view.findByText('이전 댓글 곡'); await userEvent.click(view.getByText('이전 댓글 곡')); await view.findByText('이전 댓글');
    await userEvent.type(view.getByLabelText('댓글 내용'), '이전 초안'); await userEvent.click(view.getByRole('button', { name: '댓글 등록' })); await waitFor(() => assert.equal(oldPosts, 1));
    await userEvent.click(view.getByRole('button', { name: '댓글 더 불러오기' })); await waitFor(() => assert.equal(oldCommentReads, 2));
    fireEvent.change(view.getByLabelText('빠른 밴드 전환'), { target: { value: '200' } }); await view.findByRole('button', { name: /새 댓글 밴드/ }); await userEvent.click(view.getByRole('link', { name: '곡' }));
    await view.findByText('새 댓글 곡'); await userEvent.click(view.getByText('새 댓글 곡')); await view.findByText('새 댓글');
    assert.equal(view.getByLabelText('댓글 내용').value, ''); assert.equal(view.queryByRole('button', { name: '댓글 더 불러오기' }), null); assert.equal(view.queryByRole('alert'), null);
    await act(async () => { oldMore.resolve(response({ comments: [{ ...oldComment, id: 1004, content: '늦은 이전 댓글' }], pagination: { has_more: true, next_offset: 40 } })); oldPost.resolve(response({}, 403)); });
    await waitFor(() => assert.equal(newCommentReads, 1));
    assert.equal(view.queryByText('이전 댓글'), null); assert.equal(view.queryByText('늦은 이전 댓글'), null); assert.ok(view.getByText('새 댓글'));
    assert.equal(view.getByLabelText('댓글 내용').value, ''); assert.equal(view.queryByRole('button', { name: '댓글 더 불러오기' }), null); assert.equal(view.queryByRole('alert'), null); assert.equal(oldCommentReads, 2); assert.equal(oldPosts, 1);
    view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('visitor sees media scores and comments but cannot issue engagement writes', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.includes('/by-slug/visitor-engagement')) return response({ band: { id: 120, slug: 'visitor-engagement', name: '공개 밴드', default_visibility: 'PUBLIC', membership: null } });
    if (url.includes('/songs?')) return response({ songs: [{ id: 121, title: '공개 곡', artist: '밴드' }], pagination: { has_more: false } });
    if (url.includes('/songs/121/media?')) return response({ media: [{ id: 122, file_type: 'audio', vote_score: 3, playback: { audio_url: 'https://media.invalid/public.m4a' } }], pagination: { has_more: false } });
    if (url.includes('/media/122/comments?')) return response({ comments: [{ id: 123, author: '멤버', content: '공개 댓글', parent_id: null, created_at: '2026-09-01T00:00:00Z' }], pagination: { has_more: false } });
    return response({});
  };
  try {
    const visitor = { user: null, getAccessToken: async () => null, refreshSession: async () => null, signOut: async () => {} }; const view = render(createElement(App, { entry: '/bands/visitor-engagement/songs', authValue: visitor, bandValue: { activeBands: [] }, songs: true }));
    await view.findByText('공개 곡'); await userEvent.click(view.getByText('공개 곡')); await view.findByText('점수 3'); await view.findByText('공개 댓글'); assert.equal(view.queryByRole('button', { name: '추천' }), null); assert.equal(view.queryByRole('button', { name: '댓글 등록' }), null); assert.ok(view.getAllByText(/로그인 후 밴드에 가입/).length >= 1); assert.equal(calls.some((call) => call.method !== 'GET'), false); view.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('a rejected media vote is generic, releases its flight, and a retry refetches authority', async () => {
  await load(); const previousFetch = globalThis.fetch; const first = deferred(); let puts = 0; let reads = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/vote-retry')) return response({ band: { id: 130, slug: 'vote-retry', name: '재시도', default_visibility: 'PUBLIC', membership: { id: 13, role: 'member' } } });
    if (url.includes('/songs?')) return response({ songs: [{ id: 131, title: '재시도 곡', artist: '밴드' }], pagination: { has_more: false } });
    if (url.includes('/songs/131/media?')) { reads += 1; return response({ media: [{ id: 132, file_type: 'audio', vote_score: reads > 1 ? 1 : 0, viewer_vote: reads > 1 ? 1 : 0, playback: { audio_url: 'https://media.invalid/a.m4a' } }], pagination: { has_more: false } }); }
    if (url.includes('/media/132/comments?')) return response({ comments: [], pagination: { has_more: false } });
    if (url.includes('/media/132/vote') && options.method === 'PUT') { puts += 1; return puts === 1 ? first.promise : response({ media: { id: 132, vote_score: 1, viewer_vote: 1 }, viewer_vote: 1 }); }
    return response({});
  };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  try { const view = render(createElement(App, { entry: '/bands/vote-retry/songs', authValue: member, bandValue: { activeBands: [] }, songs: true })); await view.findByText('재시도 곡'); await userEvent.click(view.getByText('재시도 곡')); const up = await view.findByRole('button', { name: '추천' }); fireEvent.click(up); fireEvent.click(up); await waitFor(() => assert.equal(puts, 1)); await act(async () => { first.resolve(response({}, 403)); }); await view.findByRole('alert'); assert.equal(up.getAttribute('aria-pressed'), 'false'); fireEvent.click(up); await waitFor(() => assert.equal(puts, 2)); await waitFor(() => assert.equal(view.getByRole('button', { name: '추천' }).getAttribute('aria-pressed'), 'true')); view.unmount(); } finally { globalThis.fetch = previousFetch; }
});
