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

let vite; let workspace; let auth; let bands;
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const load = async () => { if (workspace) return; vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); workspace = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); bands = await vite.ssrLoadModule('/src/context/BandContext.jsx'); };
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());

const App = ({ entry, authValue, bandValue, songs = false, rehearsals = false }) => createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: bandValue }, createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) }, songs ? createElement(Route, { path: 'songs', element: createElement(workspace.SongWorkspace) }) : null, rehearsals ? createElement(Route, { path: 'rehearsals', element: createElement(workspace.RehearsalWorkspace) }) : null)))));

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
  const tree = createElement(auth.AuthContext.Provider, { value: authValue }, createElement(bands.BandContext.Provider, { value: { activeBands: [] } }, createElement(MemoryRouter, { initialEntries: ['/bands/band-a/songs'] }, createElement(Go), createElement(Routes, null, createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) }, createElement(Route, { path: 'songs', element: createElement(workspace.SongWorkspace) }))))));
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
  globalThis.fetch = async (url, options = {}) => { if (url.includes('/by-slug/practice')) return response({ band: { id: 10, slug: 'practice', name: 'Practice', default_visibility: 'MEMBERS', membership: { id: 10, role: 'member' } } }); if (url.includes('/rehearsals') && options.method === 'POST') { writes.push(JSON.parse(options.body)); return response({ rehearsal: {} }, 201); } if (url.includes('/rehearsals?')) return response({ rehearsals: [], pagination: { total: 0, has_more: false } }); if (url.includes('/songs?')) return response({ songs: [{ id: 31, title: '같은 밴드 곡', artist: '밴드' }], pagination: { total: 1, has_more: false } }); return response({}); };
  const member = { user: { id: 'member' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
  const view = render(createElement(App, { entry: '/bands/practice/rehearsals', authValue: member, bandValue: { activeBands: [] }, rehearsals: true })); await view.findByRole('button', { name: '합주 추가' }); await userEvent.click(view.getByRole('button', { name: '합주 추가' })); await userEvent.type(view.getByLabelText('제목'), '9월 합주'); await userEvent.type(view.getByLabelText('날짜'), '2026-09-27'); await userEvent.click(view.getByLabelText(/같은 밴드 곡/)); await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.equal(writes.length, 1));
  assert.deepEqual(writes[0].song_ids, [31]); assert.equal(writes[0].visibility, 'MEMBERS'); assert.equal('band_id' in writes[0], false); assert.equal('created_by_membership_id' in writes[0], false); view.unmount(); globalThis.fetch = previousFetch;
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
  await view.findByText('720p 영상'); await userEvent.click(view.getByRole('button', { name: '수정' })); assert.equal(view.getByLabelText('제목').value, 'A 선택 곡');
  view.rerender(createElement(App, { entry: '/bands/shared/songs', authValue: account('account-b'), bandValue: { activeBands: [] }, songs: true }));
  assert.equal(view.queryByDisplayValue('A 선택 곡'), null); assert.equal(view.queryByText('A 다음 페이지'), null); assert.equal(view.queryByText('720p 영상'), null);
  await view.findByText('B 새 곡'); assert.equal(view.queryByText('A 선택 곡'), null); view.unmount(); globalThis.fetch = previousFetch;
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

test('rejected PATCH and DELETE show generic errors while their controls remain usable', async () => {
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
    await userEvent.click(view.getByRole('button', { name: '취소' })); await userEvent.click(view.getByRole('button', { name: '삭제' })); await view.findByText('삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.'); assert.equal(view.getByRole('button', { name: '삭제' }).disabled, false); await userEvent.click(view.getByRole('button', { name: '수정' })); await view.findByRole('button', { name: '저장' }); view.unmount();
  } finally { window.confirm = previousConfirm; globalThis.fetch = previousFetch; }
});

test('rehearsal edit uses the current band song choices and an identity-free PATCH payload', async () => {
  await load(); const previousFetch = globalThis.fetch; const writes = [];
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/edit-practice')) return response({ band: { id: 70, slug: 'edit-practice', name: '합주 밴드', default_visibility: 'MEMBERS', membership: { id: 70, role: 'member' } } });
    if (url.endsWith('/rehearsals/71') && options.method === 'PATCH') { writes.push(JSON.parse(options.body)); return response({ rehearsal: {} }); }
    if (url.includes('/rehearsals?')) return response({ rehearsals: [{ id: 71, title: '이전 합주', date: '2026-09-20', visibility: 'PUBLIC', can_edit: true, songs: [{ id: 72, title: '이전 밴드 곡' }] }], pagination: { has_more: false } });
    if (url.includes('/songs?')) return response({ songs: [{ id: 72, title: '이전 밴드 곡', artist: '밴드' }, { id: 73, title: '현재 밴드 곡', artist: '밴드' }], pagination: { has_more: false } });
    if (url.includes('/rehearsals/71/media?')) return response({ media: [], pagination: { has_more: false } });
    return response({});
  };
  const member = { user: { id: 'editor' }, getAccessToken: async () => 'editor', refreshSession: async () => 'editor', signOut: async () => {} }; const view = render(createElement(App, { entry: '/bands/edit-practice/rehearsals', authValue: member, bandValue: { activeBands: [] }, rehearsals: true }));
  await view.findByText('이전 합주'); await userEvent.click(view.getByText('이전 합주')); await userEvent.click(view.getByRole('button', { name: '수정' })); await view.findByLabelText(/현재 밴드 곡/); await userEvent.click(view.getByLabelText(/이전 밴드 곡/)); await userEvent.click(view.getByLabelText(/현재 밴드 곡/)); await userEvent.type(view.getByLabelText('제목'), ' 수정'); await userEvent.click(view.getByRole('button', { name: '저장' })); await waitFor(() => assert.equal(writes.length, 1));
  assert.deepEqual(writes[0], { title: '이전 합주 수정', date: '2026-09-20', start_date: null, end_date: null, time: '', location: '', memo: '', color: '', visibility: 'PUBLIC', song_ids: [73] }); assert.equal('band_id' in writes[0], false); assert.equal('created_by_membership_id' in writes[0], false); view.unmount(); globalThis.fetch = previousFetch;
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
            createElement(Route, { path: 'songs', element: createElement(workspace.SongWorkspace) }))))));
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
