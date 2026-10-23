import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement, Fragment } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};

let vite; let workspace; let auth; let bands; let upload;
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const authValue = { user: { id: 'owner-1' }, getAccessToken: async () => 'token', refreshSession: async () => 'token', signOut: async () => {} };
const uploadValue = { watch: () => () => {}, retryAudio: async () => {} };
const band = { id: 41, slug: 'media-band', name: '미디어 밴드', membership: { role: 'owner' }, permissions: {} };
const load = async () => {
  if (workspace) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  workspace = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx');
  auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
  upload = await vite.ssrLoadModule('/src/context/UploadContext.jsx');
};
const app = (entry) => createElement(auth.AuthContext.Provider, { value: authValue },
  createElement(bands.BandContext.Provider, { value: { activeBands: [] } },
    createElement(upload.UploadContext.Provider, { value: uploadValue },
      createElement(MemoryRouter, { initialEntries: [entry] }, createElement(Routes, null,
        createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) },
          createElement(Route, { index: true, element: createElement('p', null, '밴드 대시보드') }),
          createElement(Route, { path: 'songs', element: createElement('p', null, '곡 경로') }),
          createElement(Route, { path: 'representative-media', element: createElement(workspace.RepresentativeMediaWorkspace) }),
        ),
        createElement(Route, { path: '/account', element: createElement('p', null, '계정 경로') }),
      )),
    ),
  ),
);

after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());

test('representative media uses the normal featured playback serializer and safe candidate selection contract', async () => {
  await load(); const previousFetch = globalThis.fetch; const requests = []; let featuredReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); const method = options.method || 'GET'; requests.push({ path, method, body: options.body });
    if (path.includes('/by-slug/media-band')) return response({ band });
    if (path.includes('/bands/41/featured-media?')) {
      featuredReads += 1;
      const playbackUrl = featuredReads <= 1 ? null : featuredReads === 2 ? 'https://media.invalid/feature.mp4' : `https://media.invalid/feature-${featuredReads}.mp4`;
      return response({ media: playbackUrl ? [{ id: 701, file_type: 'video', display_name: '합주 영상.mp4', created_at: '2026-09-27T00:00:00Z', playback: { video_url: playbackUrl }, song_summary: { id: 601, title: '파란 밤', artist: '미디어 밴드' }, can_feature: true }] : [], pagination: { has_more: false } });
    }
    if (path.includes('/bands/41/songs?')) return response({ songs: [{ id: 601, title: '파란 밤', artist: '미디어 밴드', can_feature: true }], pagination: { has_more: false } });
    if (path.includes('/featured-media/candidates?')) return response({ media: [{ id: 701, song_id: 601, file_type: 'video', display_name: '합주 영상.mp4', created_at: '2026-09-27T00:00:00Z', is_featured: false, can_feature: true }], pagination: { has_more: false } });
    if (path.includes('/songs/601/media?')) return response({ media: [{ id: 701, file_type: 'video', display_name: '합주 영상.mp4', created_at: '2026-09-27T00:00:00Z', playback: { video_url: 'https://media.invalid/source.mp4' } }], pagination: { has_more: false } });
    if (path.endsWith('/songs/601/featured-media') && method === 'PUT') return response({ song_id: 601, media_id: 701, is_featured: true, can_feature: true });
    return response({});
  };
  try {
    const view = render(app('/bands/media-band/representative-media'));
    await view.findByText('등록된 대표 미디어가 없습니다.');
    assert.equal(view.getByRole('link', { name: '대표 미디어 관리' }).getAttribute('href'), '/bands/media-band/manage/representative-media');
    assert.equal(view.container.querySelector('video'), null);
    fireEvent.change(view.getByRole('combobox', { name: '곡 선택' }), { target: { value: '601' } });
    await view.findByText('합주 영상.mp4');
    await userEvent.click(view.getByRole('button', { name: '재생 미리보기' }));
    await waitFor(() => assert.equal(view.container.querySelector('video')?.getAttribute('src'), 'https://media.invalid/source.mp4'));
    assert.equal(view.container.querySelector('video')?.getAttribute('preload'), 'none');
    await userEvent.click(view.getByRole('button', { name: '대표로 지정' }));
    await waitFor(() => assert.equal(featuredReads, 2));
    await userEvent.click(view.getByRole('button', { name: '미리보기 닫기' }));
    assert.equal(view.container.querySelector('video'), null);
    assert.equal(view.getAllByText('2026년 9월 27일').length, 2);
    await userEvent.click(view.getByRole('button', { name: '재생', exact: true }));
    await waitFor(() => assert.equal(view.container.querySelector('video')?.getAttribute('src'), 'https://media.invalid/feature.mp4'));
    fireEvent.error(view.container.querySelector('video'));
    await waitFor(() => assert.equal(view.container.querySelector('video')?.getAttribute('src'), 'https://media.invalid/feature-3.mp4'));
    fireEvent.error(view.container.querySelector('video'));
    await waitFor(() => assert.equal(view.container.querySelector('video')?.getAttribute('src'), 'https://media.invalid/feature-4.mp4'));
    const save = requests.find((request) => request.method === 'PUT');
    assert.equal(save.path, 'http://localhost:5000/api/v2/bands/41/songs/601/featured-media');
    assert.deepEqual(JSON.parse(save.body), { media_id: 701 });
    assert.equal(save.body.includes('url'), false);
    assert.equal(requests.some((request) => request.path.includes('candidates') && request.path.includes('video_url')), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('candidate selection stays available when the normal playback read is forbidden', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const path = String(url); calls.push({ path, method: options.method || 'GET' });
    if (path.includes('/by-slug/media-band')) return response({ band });
    if (path.includes('/featured-media?')) return response({ media: [], pagination: { has_more: false } });
    if (path.includes('/songs?')) return response({ songs: [{ id: 601, title: '파란 밤', artist: '미디어 밴드', can_feature: true }], pagination: { has_more: false } });
    if (path.includes('/featured-media/candidates?')) return response({ media: [{ id: 702, song_id: 601, file_type: 'audio', display_name: '멤버 전용 믹스.m4a', is_featured: false, can_feature: true }], pagination: { has_more: false } });
    if (path.includes('/songs/601/media?')) return response({ code: 'forbidden' }, 403);
    if (path.endsWith('/songs/601/featured-media') && options.method === 'PUT') return response({ song_id: 601, media_id: 702, is_featured: true, can_feature: true });
    return response({});
  };
  try {
    const view = render(app('/bands/media-band/representative-media'));
    await view.findByText('등록된 대표 미디어가 없습니다.');
    fireEvent.change(view.getByRole('combobox', { name: '곡 선택' }), { target: { value: '601' } });
    await view.findByText('멤버 전용 믹스.m4a');
    assert.ok(view.getByRole('button', { name: '대표로 지정' }));
    assert.ok(view.getByText(/후보 지정은 계속할 수 있습니다/));
    await userEvent.click(view.getByRole('button', { name: '대표로 지정' }));
    await waitFor(() => assert.ok(calls.some((call) => call.method === 'PUT')));
    assert.equal(view.queryByRole('button', { name: '재생 미리보기' }), null);
  } finally { globalThis.fetch = previousFetch; }
});

test('v2 video playback with a safe audio_url enables radio mode without transcoding retry or status polling', async () => {
  await load(); const previousFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url) => {
    const path = String(url); calls.push(path);
    if (path.includes('/by-slug/media-band')) return response({ band });
    if (path.includes('/bands/41/featured-media?')) return response({ media: [{ id: 709, file_type: 'video', display_name: '공연 영상.mp4', playback: { video_url: 'https://media.invalid/private-video.mp4', audio_url: 'https://media.invalid/private-audio.m4a' }, song_summary: { id: 609, title: '라디오의 밤', artist: '미디어 밴드' }, can_feature: true }], pagination: { has_more: false } });
    if (path.includes('/bands/41/songs?')) return response({ songs: [], pagination: { has_more: false } });
    throw new Error(`unexpected ${path}`);
  };
  try {
    const view = render(app('/bands/media-band/representative-media'));
    await view.findByText('라디오의 밤');
    await userEvent.click(view.getByRole('button', { name: '재생', exact: true }));
    const radioMode = view.getByRole('button', { name: '라디오 (데이터 절약)' });
    assert.equal(radioMode.disabled, false);
    await userEvent.click(radioMode);
    await waitFor(() => assert.equal(view.container.querySelector('audio')?.getAttribute('src'), 'https://media.invalid/private-audio.m4a'));
    assert.equal(view.queryByRole('button', { name: /재시도/ }), null);
    assert.equal(view.queryByText(/음원을 추출 중/), null);
    assert.equal(calls.some((path) => path.includes('/processing')), false);
    assert.equal(calls.some((path) => path.includes('/retry')), false);
  } finally { globalThis.fetch = previousFetch; }
});

test('resolved band title survives nested workspace routes and resets outside the workspace', async () => {
  await load(); const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url) => String(url).includes('/by-slug/media-band') ? response({ band }) : response({});
  try {
    const view = render(app('/bands/media-band'));
    await view.findByText('밴드 대시보드');
    await waitFor(() => assert.equal(document.title, '미디어 밴드 · Band Archive'));
    await userEvent.click(view.getByRole('link', { name: '곡' }));
    await view.findByText('곡 경로');
    await waitFor(() => assert.equal(document.title, '미디어 밴드 · Band Archive'));
    await userEvent.click(view.getByRole('link', { name: '대표 미디어' }));
    await view.findByText('등록된 대표 미디어가 없습니다.');
    await waitFor(() => assert.equal(document.title, '미디어 밴드 · Band Archive'));
    await userEvent.click(view.getByRole('link', { name: '내 계정' }));
    await view.findByText('계정 경로');
    await waitFor(() => assert.equal(document.title, 'Band Archive'));
  } finally { globalThis.fetch = previousFetch; }
});
