import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { cleanup, render, waitFor } from '@testing-library/react';
import { createElement, StrictMode } from 'react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { fireEvent } from '@testing-library/react';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, FormData: dom.window.FormData, requestAnimationFrame: (callback) => setTimeout(callback, 0) });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
let vite; let routes; let auth; let bands; let uploads;
const load = async () => {
  if (routes) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  routes = (await vite.ssrLoadModule('/src/App.jsx')).AppRoutes;
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
  uploads = await vite.ssrLoadModule('/src/context/UploadContext.jsx');
};
after(async () => { await vite?.close(); dom.window.close(); }); afterEach(() => cleanup());

const Location = () => { const location = useLocation(); return createElement('output', { 'data-testid': 'route' }, `${location.pathname}${location.search}${location.hash}`); };
const AccountBands = ({ children, authValue, entry, strict = false }) => {
  const tree = createElement(auth.AuthContext.Provider, { value: authValue },
    createElement(bands.BandProvider, null,
      createElement(uploads.UploadProvider, null,
        createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Location), children))));
  return strict ? createElement(StrictMode, null, tree) : tree;
};
const account = (id = null) => ({ user: id ? { id, email: 'person@example.test' } : null, getAccessToken: async () => 'test-token', refreshSession: async () => 'test-token', signOut: async () => {}, loading: false, configError: null, hasRecoverySession: false });
const renderRoutes = (entry, authValue = account(), strict = false) => render(createElement(AccountBands, { entry, authValue, strict }, createElement(routes)));

test('upload target opens the original band song detail through the real AppRoutes tree', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  const original = { id: 41, name: '원래 밴드', slug: 'origin-band', membership: { role: 'member' } };
  const selected = { id: 42, name: '선택 밴드', slug: 'selected-band', membership: { role: 'member' } };
  globalThis.fetch = async (url) => {
    const path = String(url); calls.push(path);
    if (path.endsWith('/api/v2/bands')) return response({ bands: [selected, original], pending_memberships: [] });
    if (path.includes('/by-slug/origin-band')) return response({ band: original });
    if (path.includes('/by-slug/selected-band')) return response({ band: selected });
    if (path.endsWith('/bands/41/songs/7')) return response({ song: { id: 7, band_id: 41, title: '원래 밴드 대상 곡', artist: '원래 연주자', status: 'Practice' } });
    if (path.includes('/songs/7/media?')) return response({ media: [], pagination: { has_more: false } });
    if (path.includes('/songs?')) return response({ songs: [], pagination: { has_more: false } });
    return response({});
  };
  try {
    const task = { id: 'origin-task', name: 'practice.mp4', bandId: 41, bandSlug: 'origin-band', bandName: original.name, target: { bandId: 41, songId: 7 }, targetLabel: '곡 #7', status: 'completed', mediaId: 99 };
    const view = render(createElement(auth.AuthContext.Provider, { value: account('upload-route-user') }, createElement(bands.BandProvider, null,
      createElement(uploads.UploadContext.Provider, { value: { tasks: [task], nativePending: [], subscribeMediaCompletion: () => () => {} } },
        createElement(MemoryRouter, { initialEntries: ['/bands/selected-band/songs'] }, createElement(Location), createElement(routes))))));
    await view.findByRole('heading', { name: '곡 목록' });
    fireEvent.click(view.getByRole('button', { name: /업로드함/ }));
    fireEvent.click(view.getByRole('button', { name: '대상 열기' }));
    await view.findByRole('heading', { name: '원래 밴드 대상 곡' });
    assert.equal(view.getByTestId('route').textContent, '/bands/origin-band/songs?song=7');
    assert.equal(calls.some((path) => path.endsWith('/bands/41/songs/7')), true);
    assert.equal(calls.some((path) => path.endsWith('/bands/42/songs/7')), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('anonymous home shows an archive entry card without fetching archive contents', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => { calls.push([new URL(url).pathname + new URL(url).search, options.headers]); return response({ media: [] }); };
  try {
    const view = renderRoutes('/', account());
    await view.findByRole('heading', { name: '함께 만든 순간을 오래 남기다' });
    assert.ok(view.getByText(/실제 밴드 정보가 아닌 화면 예시입니다/));
    assert.ok(view.getByLabelText('이메일'));
    assert.equal(view.queryByRole('navigation', { name: '밴드 공간' }), null);
    assert.equal(view.queryByRole('button', { name: /곡 목록|멤버|갤러리|다음 곡 추천/ }), null);
    const archiveLink = view.getByRole('link', { name: '공개 아카이브 열기' });
    assert.equal(archiveLink.getAttribute('href'), '/archive');
    assert.ok(archiveLink.closest('.portal-card'));
    assert.equal(view.queryByRole('heading', { name: '공개 영상·음원' }), null);
    assert.equal(calls.some(([url]) => url.startsWith('/api/v2/public/archive/')), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('anonymous browser routes are gated before discover or workspace data can mount', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); throw new Error('private route should not fetch'); };
  try {
    for (const path of ['/bands/discover', '/bands/quiet-lantern/songs', '/bands/quiet-lantern/manage/logo', '/bands/private-archive/manage/representative-media']) {
      const view = renderRoutes(path, account());
      await view.findByRole('heading', { name: '로그인' });
      await waitFor(() => assert.match(view.getByTestId('route').textContent, /^\/login\?/));
      assert.equal(view.queryByRole('heading', { name: '공개 밴드' }), null);
      assert.equal(view.queryByRole('navigation', { name: '밴드 공간' }), null);
      view.unmount();
    }
    assert.deepEqual(calls, []);
  } finally { globalThis.fetch = previousFetch; }
});

test('private-band representative media management route uses its narrow server catalog without resolving workspace membership', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => {
    const path = String(url); calls.push(path);
    if (path.endsWith('/api/v2/bands')) return response({ bands: [], pending_memberships: [] });
    if (path.includes('/api/v2/bands/by-slug/private-archive/manage/representative-media?')) return response({
      band: { id: 812, slug: 'private-archive', name: '비공개 아카이브', can_feature: true },
      songs: [{ id: 923, title: '숨은 파도', artist: '아카이브 밴드', can_feature: true }],
      pagination: { limit: 20, offset: 0, total: 1, has_more: false, next_offset: null },
    });
    throw new Error(`unexpected route read ${path}`);
  };
  try {
    const view = renderRoutes('/bands/private-archive/manage/representative-media', account('platform-operator'));
    await view.findByRole('heading', { name: '대표 미디어 관리' });
    await view.findByRole('option', { name: '숨은 파도 · 아카이브 밴드' });
    assert.ok(view.getByRole('link', { name: '계정과 밴드' }));
    assert.equal(calls.some((path) => path.includes('/api/v2/bands/by-slug/private-archive') && !path.includes('/manage/representative-media')), false);
    assert.equal(calls.some((path) => /\/api\/v2\/bands\/\d+\/(?:songs|featured-media)/.test(path)), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('platform request route requires login and denies an authenticated non-admin before reading the queue', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ user: { is_platform_admin: false } }); };
  try {
    const anonymous = renderRoutes('/platform/band-requests', account());
    await anonymous.findByRole('heading', { name: '로그인' });
    assert.equal(calls.length, 0);
    anonymous.unmount();

    const member = renderRoutes('/platform/band-requests', account('member'));
    await member.findByText('플랫폼 관리자만 이 페이지를 사용할 수 있습니다.');
    assert.ok(calls.some((url) => url.endsWith('/api/v2/me')));
    assert.equal(calls.some((url) => url.includes('/api/v2/platform/band-creation-requests?')), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('authenticated root opens the portal while account data remains outside its content', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => { calls.push(String(url)); return response({ bands: [], pending_memberships: [] }); };
  try {
    const view = renderRoutes('/', account('member-1'), true);
    await view.findByRole('heading', { name: /오늘은 어떤 음악을 함께 만들어 볼까요/ });
    assert.equal(calls.filter((url) => url.endsWith('/api/v2/bands')).length, 1);
    assert.equal(calls.some((url) => url.endsWith('/api/v2/me')), false);
    assert.equal(calls.some((url) => url.includes('legacy-ownership-claim')), false);
    assert.equal(view.queryByRole('heading', { name: '계정과 밴드 관리' }), null);
    const archiveCard = view.getByRole('link', { name: '공개 아카이브 열기' }).closest('.portal-card');
    assert.equal(archiveCard?.parentElement, view.getByRole('heading', { name: '이용 안내' }).closest('.portal-card')?.parentElement);
    assert.equal(calls.some((url) => url.includes('/api/v2/public/archive/')), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('guide is public and community writing actions announce the unavailable feature without making requests', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => { calls.push([String(url), options]); return response({ bands: [], pending_memberships: [] }); };
  try {
    const guide = renderRoutes('/guide', account());
    await guide.findByRole('heading', { name: '계정을 만들고 이메일을 확인해요' });
    assert.ok(guide.getByText(/페이지를 닫거나 이동해도 전송이 계속된다고 보장되지 않으니/));
    assert.ok(guide.getByText(/멤버십 및 서버가 확인한 권한/));
    guide.unmount();

    const board = renderRoutes('/community/recruiting', account('member-1'));
    await board.findByRole('heading', { name: '밴드 구인구직' });
    fireEvent.click(board.getByRole('button', { name: '글쓰기' }));
    assert.equal(board.getAllByRole('status').some((item) => item.textContent.includes('준비중입니다!')), true);
    assert.equal(board.queryByRole('textbox'), null);
    assert.equal(calls.some(([, options]) => options.method && options.method !== 'GET'), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('band choice selects the membership and navigates to its slug in the same action', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  const team = { id: 12, name: 'Moon Archive', slug: 'moon-archive', membership: { role: 'owner' } };
  globalThis.fetch = async (url) => { calls.push(String(url)); return response(String(url).includes('/api/v2/bands/by-slug/') ? { band: team } : { bands: [team], pending_memberships: [] }); };
  try {
    const view = renderRoutes('/bands', account('member-1'));
    await view.findByRole('button', { name: /Moon Archive.*소유자/ });
    await view.getByRole('button', { name: /Moon Archive.*소유자/ }).click();
    await waitFor(() => assert.equal(view.getByTestId('route').textContent, '/bands/moon-archive'));
    assert.equal(window.localStorage.getItem('band-archive.selected-band:member-1'), '12');
    await waitFor(() => assert.ok(calls.some((url) => url.includes('/api/v2/bands/by-slug/moon-archive'))));
  } finally { globalThis.fetch = previousFetch; }
});

test('one-band account switcher starts unselected and opens the sole membership', async () => {
  await load(); const previousFetch = globalThis.fetch; const team = { id: 12, name: 'Moon Archive', slug: 'moon-archive', membership: { role: 'owner' } };
  globalThis.fetch = async (url) => response(String(url).includes('/api/v2/bands/by-slug/') ? { band: team } : { bands: [team], pending_memberships: [] });
  try {
    const view = renderRoutes('/bands', account('one-band-user'));
    const switcher = await view.findByRole('combobox', { name: '빠른 밴드 전환' });
    assert.equal(switcher.value, '');
    fireEvent.change(switcher, { target: { value: '12' } });
    await waitFor(() => assert.equal(view.getByTestId('route').textContent, '/bands/moon-archive'));
  } finally { globalThis.fetch = previousFetch; }
});

test('workspace navigation stays on one horizontal scroll row at narrow widths', () => {
  const css = readFileSync(new URL('../workspace/BandWorkspace.css', import.meta.url), 'utf8');
  assert.match(css, /\.workspace-header nav\{[^}]*flex-wrap:nowrap[^}]*overflow-x:auto/);
  assert.match(css, /@media \(max-width: 900px\)/);
  assert.match(css, /\.workspace-actions>\.header-actions\{flex:1 1 280px\}/);
  assert.match(css, /\.workspace-actions\{[^}]*flex-wrap:wrap/);
  assert.doesNotMatch(css, /@media[^}]*workspace-header nav[^}]*display:\s*none/);
});

test('a direct workspace link keeps its URL band selected and switches without a second membership read', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  const direct = { id: 21, name: 'Direct Band', slug: 'direct-band', membership: { role: 'owner' }, permissions: { can_view_roster: true } };
  const other = { id: 22, name: 'Other Band', slug: 'other-band', membership: { role: 'member' }, permissions: { can_view_roster: true } };
  globalThis.fetch = async (url) => {
    const path = String(url); calls.push(path);
    if (path.endsWith('/api/v2/bands')) return response({ bands: [direct, other], pending_memberships: [] });
    if (path.includes('/api/v2/bands/by-slug/direct-band')) return response({ band: direct });
    if (path.includes('/api/v2/bands/by-slug/other-band')) return response({ band: other });
    if (path.includes('/api/v2/bands/21/songs')) return response({ songs: [], pagination: { total: 0 } });
    if (path.includes('/api/v2/bands/21/rehearsals')) return response({ rehearsals: [], pagination: { total: 0 } });
    if (path.includes('/api/v2/bands/21/announcement')) return response({ announcement: null });
    return response({});
  };
  window.localStorage.setItem('band-archive.selected-band:direct-link-user', '22');
  try {
    const view = renderRoutes('/bands/direct-band/songs', account('direct-link-user'));
    const switcher = await view.findByRole('combobox', { name: '빠른 밴드 전환' });
    await waitFor(() => assert.equal(switcher.value, '21'));
    fireEvent.change(switcher, { target: { value: '22' } });
    await waitFor(() => assert.equal(view.getByTestId('route').textContent, '/bands/other-band'));
    assert.equal(calls.filter((path) => path.endsWith('/api/v2/bands')).length, 1);
    assert.ok(calls.some((path) => path.includes('/api/v2/bands/by-slug/other-band')));
  } finally { globalThis.fetch = previousFetch; }
});

test('legacy routes go to the landing, while the singular band alias retains only its safe workspace path', async () => {
  await load(); const previousFetch = globalThis.fetch;
    globalThis.fetch = async (url) => response(String(url).includes('/api/v2/bands/by-slug/') ? {} : { bands: [], pending_memberships: [] });
  try {
    for (const path of ['/songs/43', '/members', '/gallery', '/suggestions', '/not-a-route']) {
      const old = renderRoutes(path, account());
      await old.findByRole('heading', { name: '함께 만든 순간을 오래 남기다' });
      assert.equal(old.getByTestId('route').textContent, '/'); old.unmount();
    }

    const alias = renderRoutes('/band/quiet-lantern/songs?sort=recent#top', account());
    await alias.findByRole('heading', { name: '로그인' });
      await waitFor(() => assert.equal(alias.getByTestId('route').textContent, '/login?returnTo=%2Fbands%2Fquiet-lantern%2Fsongs'));
    alias.unmount();

    const previousCalls = [];
    globalThis.fetch = async (url) => { previousCalls.push(String(url)); return response({}); };
    const signedInAlias = renderRoutes('/band/quiet-lantern/songs?sort=recent#top', account('member-1'));
    await waitFor(() => assert.equal(signedInAlias.getByTestId('route').textContent, '/bands/quiet-lantern/songs'));
    await waitFor(() => assert.ok(previousCalls.some((url) => url.includes('/api/v2/bands/by-slug/quiet-lantern'))));
    signedInAlias.unmount();

    globalThis.fetch = async (url) => { previousCalls.push(String(url)); return response({ bands: [], pending_memberships: [] }); };
    const unknownWorkspace = renderRoutes('/bands/quiet-lantern/not-a-tab', account('member-1'));
    await unknownWorkspace.findByText('내 밴드');
    assert.equal(unknownWorkspace.getByTestId('route').textContent, '/account');
    unknownWorkspace.unmount();
  } finally { globalThis.fetch = previousFetch; }
});

test('anonymous public archive follows band, song and playback links without credentials', async () => {
  await load();
  const previousFetch = globalThis.fetch;
  const calls = [];
  const previousObserver = globalThis.IntersectionObserver;
  const previousClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  let copied;
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (url) => { copied = url; } } });
  let bottom;
  globalThis.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; }
    observe(node) { if (node.closest('.archive-feed-status')) bottom = this.callback; }
    disconnect() {}
  };
  const band = { slug: 'example-band', name: '공개 밴드' };
  const song = { id: 91, title: '공개 곡 제목', artist: '공개 가수' };
  const media = { id: 92, file_type: 'video', display_name: '공개 연주 영상', band, song, like_count: 3 };
  const audio = { id: 93, file_type: 'audio', display_name: '음원', band,
    song: { ...song, title: '두 번째 자료' }, rehearsal_title: '정기 합주' };
  const audioTitle = `${song.artist} - ${audio.song.title}`;
  const videoTitle = `${song.artist} - ${song.title}`;
  const pagination = { has_more: false };
  globalThis.fetch = async (url, options) => {
    const { pathname, searchParams } = new URL(url);
    calls.push({ pathname, headers: options.headers });
    switch (pathname) {
      case '/api/v2/public/archive/media': return searchParams.get('offset') === '0'
        ? response({ media: [media], pagination: { has_more: true } })
        : response({ media: [media, audio], pagination });
      case '/api/v2/public/archive/songs': return response({ songs: [{ ...song, band }], pagination });
      case '/api/v2/public/archive/bands/example-band': return response({ band, songs: [song], pagination });
      case '/api/v2/public/archive/bands/example-band/songs/91': return response({ band, song, media: [media], pagination });
      case '/api/v2/public/archive/media/92': return response({ band, song, media: { ...media, playback: { video_url: 'https://files.example.test/play.mp4', audio_url: 'https://files.example.test/play.m4a' } } });
      default: return response({}, 404);
    }
  };
  try {
    const view = renderRoutes('/', account());
    const openArchive = await view.findByRole('link', { name: '공개 아카이브 열기' });
    assert.equal(calls.length, 0);
    fireEvent.click(openArchive);
    await view.findByRole('heading', { name: '공개 아카이브', level: 1 });
    assert.equal(view.queryByRole('heading', { name: '공개 곡' }), null);
    assert.ok(calls.every(({ pathname }) => pathname !== '/api/v2/public/archive/songs'));
    await view.findByRole('button', { name: '더 보기' });
    await waitFor(() => assert.equal(typeof bottom, 'function'));
    assert.equal(view.queryByRole('link', { name: audioTitle }), null);
    bottom([{ isIntersecting: true }]);
    await view.findByRole('link', { name: audioTitle });
    assert.ok(view.getByText('정기 합주'));
    assert.equal(view.getAllByRole('link', { name: videoTitle }).length, 1);
    assert.equal(view.getByRole('link', { name: /좋아요 3/ }).getAttribute('href'), '/login?returnTo=%2Farchive%2Fmedia%2F92');
    fireEvent.click(view.getAllByRole('button', { name: '공유' })[0]);
    await waitFor(() => assert.equal(copied, 'http://localhost/archive/media/92'));
    assert.equal(calls.filter(({ pathname }) => pathname === '/api/v2/public/archive/media').length, 2);
    fireEvent.click(view.getAllByRole('link', { name: '밴드 페이지로' })[0]);
    await view.findByRole('heading', { name: band.name });
    const songLink = await view.findByRole('link', { name: song.title });
    assert.equal(songLink.classList.contains('bands-return-link'), true);
    fireEvent.click(songLink);
    await view.findByRole('heading', { name: song.title });
    fireEvent.click(await view.findByRole('link', { name: videoTitle }));
    await view.findByRole('heading', { name: media.display_name });
    assert.equal(view.queryByRole('heading', { name: '로그인' }), null);
    assert.equal(view.container.querySelector('video')?.getAttribute('src'), 'https://files.example.test/play.mp4');
    assert.equal(view.getByRole('button', { name: '영상' }).classList.contains('primary-btn'), true);
    assert.equal(view.getByRole('button', { name: '음원만 듣기' }).classList.contains('bands-return-link'), true);
    assert.ok(calls.every(({ pathname, headers }) => pathname.startsWith('/api/v2/public/archive/') && !headers.Authorization));
  } finally {
    globalThis.fetch = previousFetch;
    if (previousClipboard) Object.defineProperty(navigator, 'clipboard', previousClipboard);
    else delete navigator.clipboard;
    if (previousObserver === undefined) delete globalThis.IntersectionObserver;
    else globalThis.IntersectionObserver = previousObserver;
  }
});

test('signed-in archive visitor can like and unlike without a band membership', async () => {
  await load();
  const previousFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const { pathname, searchParams } = new URL(url);
    calls.push({ pathname, authorization: options.headers?.Authorization, method: options.method });
    if (pathname === '/api/v2/bands') return response({ bands: [], pending_memberships: [] });
    if (pathname === '/api/v2/public/archive/media') return response({
      media: [{ id: 92, file_type: 'audio', like_count: 3, song: { title: '좋아요 대상' } }],
      pagination: { has_more: false },
    });
    if (pathname === '/api/v2/public/archive/likes') {
      assert.equal(searchParams.get('media_ids'), '92');
      return response({ liked_media_ids: [] });
    }
    if (pathname === '/api/v2/public/archive/media/92/like') {
      const { liked } = JSON.parse(options.body);
      return response({ liked, like_count: liked ? 4 : 3 });
    }
    return response({}, 404);
  };
  try {
    const view = renderRoutes('/archive', account('archive-listener'));
    const like = await view.findByRole('button', { name: '좋아요 3' });
    await waitFor(() => assert.equal(like.disabled, false));
    assert.equal(like.getAttribute('aria-pressed'), 'false');
    fireEvent.click(like);
    const unlike = await view.findByRole('button', { name: '좋아요 취소 4' });
    assert.equal(unlike.getAttribute('aria-pressed'), 'true');
    fireEvent.click(unlike);
    await view.findByRole('button', { name: '좋아요 3' });
    const archiveCalls = calls.filter(({ pathname }) => pathname.startsWith('/api/v2/public/archive/'));
    assert.deepEqual(archiveCalls.map(({ authorization }) => authorization), [undefined, 'Bearer test-token', 'Bearer test-token', 'Bearer test-token']);
    assert.equal(archiveCalls.filter(({ method }) => method === 'PUT').length, 2);
  } finally { globalThis.fetch = previousFetch; }
});
