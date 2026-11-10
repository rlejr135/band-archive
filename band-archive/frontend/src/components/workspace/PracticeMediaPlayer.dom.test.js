import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { createElement as h } from 'react';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
let vite; let Player;
const load = async () => {
  if (Player) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  Player = (await vite.ssrLoadModule('/src/components/common/MediaPlayer.jsx')).default;
};
afterEach(() => cleanup());
after(async () => { await vite?.close(); dom.window.close(); });

const file = (extra = {}) => ({ id: 7, type: 'video', name: 'Practice', url: 'https://media.invalid/video.mp4', audio_url: 'https://media.invalid/audio.m4a', ...extra });
const player = (content = file(), identity = 'account-a:band-a:song-1:media-7', props = {}) => h(Player, { file: content, playbackIdentity: identity, ...props });
const ready = (media, { time = 0, duration = 100, playing = false, rejectPlay = false, playResult } = {}) => {
  let paused = !playing;
  Object.defineProperty(media, 'duration', { configurable: true, value: duration });
  Object.defineProperty(media, 'paused', { configurable: true, get: () => paused });
  media.currentTime = time;
  media.play = () => {
    if (playResult) return playResult();
    if (rejectPlay) return Promise.reject(new Error('Autoplay blocked'));
    paused = false; media.dispatchEvent(new dom.window.Event('play')); return Promise.resolve();
  };
  media.pause = () => { paused = true; media.dispatchEvent(new dom.window.Event('pause')); };
  fireEvent.loadedMetadata(media);
};
const click = (view, name) => fireEvent.click(view.getByRole('button', { name, exact: true }));
const setRate = (view, value) => fireEvent.change(view.getByRole('combobox', { name: '재생 속도' }), { target: { value } });

test('context-free player retains practice state across rapid mode changes and signed URL renewal without network requests', async () => {
  await load(); const previousFetch = globalThis.fetch; let requests = 0;
  globalThis.fetch = () => { requests += 1; throw new Error('The core must not request data'); };
  try {
    const view = render(player());
    let video = view.container.querySelector('video');
    ready(video, { time: 20, playing: true }); video.volume = 0.4;
    setRate(view, '1.5');
    click(view, '현재 위치를 A 시작으로 설정');
    video.currentTime = 23; fireEvent.timeUpdate(video); click(view, '현재 위치를 B 끝으로 설정');
    click(view, 'A-B 구간 반복');
    video.currentTime = 25; fireEvent.timeUpdate(video); assert.equal(video.currentTime, 20);
    click(view, '5초 앞으로 이동'); assert.equal(video.currentTime, 25);
    click(view, '5초 뒤로 이동'); assert.equal(video.currentTime, 20);
    click(view, '음원만 듣기'); click(view, '영상'); click(view, '음원만 듣기');
    let audio = view.container.querySelector('audio');
    await act(async () => ready(audio));
    assert.equal(audio.currentTime, 20); assert.equal(audio.volume, 0.4); assert.equal(audio.playbackRate, 1.5); assert.equal(audio.paused, false);
    assert.equal(view.getByRole('button', { name: 'A-B 구간 반복' }).getAttribute('aria-pressed'), 'true');
    audio.currentTime = 21; fireEvent.timeUpdate(audio);
    view.rerender(player(file({ name: 'Metadata changed' })));
    assert.equal(view.container.querySelector('audio') === audio, true);
    view.rerender(player(file({ audio_url: 'https://media.invalid/refreshed.m4a' })));
    const replacement = view.container.querySelector('audio'); assert.equal(replacement === audio, false);
    await act(async () => ready(replacement)); audio = replacement;
    assert.equal(audio.currentTime, 21); assert.equal(audio.volume, 0.4); assert.equal(audio.playbackRate, 1.5); assert.equal(audio.paused, false);
    audio.currentTime = 24; fireEvent.timeUpdate(audio); assert.equal(audio.currentTime, 20);
    assert.equal(requests, 0);
  } finally { globalThis.fetch = previousFetch; }
});

test('same media id resets playback for another account, band or selected detail identity', async () => {
  await load();
  for (const identity of ['account-b:band-a:song-1:media-7', 'account-a:band-b:song-1:media-7', 'account-a:band-a:rehearsal-1:media-7']) {
    const view = render(player()); ready(view.container.querySelector('video'), { time: 12 }); setRate(view, '1.25'); click(view, '음원만 듣기');
    view.rerender(player(file(), identity));
    assert.equal(view.container.querySelector('video') !== null, true);
    assert.equal(view.container.querySelector('audio') === null, true);
    assert.equal(view.getByRole('combobox', { name: '재생 속도' }).value, '1');
    assert.equal(view.getByRole('button', { name: 'A-B 구간 반복' }).getAttribute('aria-pressed'), 'false');
    assert.equal(view.getByRole('button', { name: 'A-B 구간 지우기' }).disabled, true);
    view.unmount();
  }
});

test('source availability controls video/audio mode and audio-only videos retain all practice tools', async () => {
  await load(); const view = render(player(file({ audio_url: null })));
  assert.equal(view.queryByRole('button', { name: '음원만 듣기' }) === null, true);
  view.rerender(player(file({ url: null }), 'audio-fallback'));
  assert.equal(view.container.querySelector('video') === null, true);
  assert.equal(view.container.querySelector('audio')?.getAttribute('src'), 'https://media.invalid/audio.m4a');
  ready(view.container.querySelector('audio'));
  assert.equal(view.getByRole('combobox', { name: '재생 속도' }).options.length, 4);
  assert.equal(view.getByRole('button', { name: '5초 앞으로 이동' }).disabled, false);
  assert.equal(view.getByRole('button', { name: '현재 위치를 A 시작으로 설정' }).disabled, false);
  assert.equal(view.queryByRole('button', { name: '영상', exact: true }) === null, true);
  view.rerender(player(file(), 'audio-fallback'));
  assert.equal(view.container.querySelector('audio') !== null, true);
  assert.equal(view.container.querySelector('video') === null, true);
});

test('blocked playback restoration is recoverable and an old mode play promise cannot overwrite current status', async () => {
  await load(); const view = render(player());
  ready(view.container.querySelector('video'), { time: 12, playing: true }); click(view, '음원만 듣기');
  await act(async () => ready(view.container.querySelector('audio'), { rejectPlay: true }));
  await view.findByText(/브라우저가 자동 재생을 허용하지 않았습니다/);
  assert.equal(view.container.querySelector('audio').currentTime, 12);
  assert.equal(view.getByRole('combobox', { name: '재생 속도' }).value, '1');
  click(view, '영상'); ready(view.container.querySelector('video'));
  assert.equal(view.container.querySelector('video').currentTime, 12);
  await waitFor(() => assert.equal(view.container.querySelector('audio') === null, true));

  const video = view.container.querySelector('video'); ready(video, { time: 15, playing: true });
  click(view, '음원만 듣기'); const oldAudio = view.container.querySelector('audio');
  let rejectOldPlay;
  ready(oldAudio, { playResult: () => new Promise((resolve, reject) => { rejectOldPlay = reject; }) });
  click(view, '영상'); ready(view.container.querySelector('video'));
  click(view, '5초 앞으로 이동');
  const currentStatus = view.container.querySelector('.practice-status').textContent;
  assert.equal(typeof rejectOldPlay, 'function');
  await act(async () => rejectOldPlay(new Error('Stale mode rejection')));
  assert.equal(view.container.querySelector('.practice-status').textContent, currentStatus);
});
