import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event, requestAnimationFrame: (callback) => setTimeout(callback, 0) });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};

let vite; let upload; let uploadContext;
const load = async () => {
  if (upload) return;
  vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  upload = await vite.ssrLoadModule('/src/components/common/UploadCenter.jsx');
  uploadContext = await vite.ssrLoadModule('/src/context/UploadContext.jsx');
};
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());

test('UploadCenter labels a completed task with its immutable origin band and opens that band after a switch', async () => {
  await load();
  const task = {
    id: 'band-a-task', name: 'practice.mp4', bandId: 41, bandName: '첫 밴드', bandSlug: 'first-band',
    target: { bandId: 41, songId: 7 }, targetLabel: '곡 #7', status: 'completed', mediaId: 99,
  };
  const context = { tasks: [task], cancel: () => {}, retryTask: async () => {}, clearHistory: () => {} };
  const Location = () => createElement('output', { 'data-testid': 'location' }, useLocation().pathname);
  const view = render(createElement(MemoryRouter, { initialEntries: ['/bands/second-band/songs'] },
    createElement(uploadContext.UploadContext.Provider, { value: context }, createElement(upload.default), createElement(Location)),
  ));
  await userEvent.click(view.getByRole('button', { name: /업로드함/ }));
  await view.findByText('첫 밴드 · 곡 #7');
  await act(async () => { await userEvent.click(view.getByRole('button', { name: '대상 열기' })); });
  await waitFor(() => assert.equal(view.getByTestId('location').textContent, '/bands/first-band/songs/7'));
});

test('UploadCenter does not offer a band task the unscoped legacy audio retry route', async () => {
  await load();
  const task = {
    id: 'band-retry', name: 'practice.mp3', bandId: 41, bandName: '첫 밴드', bandSlug: 'first-band',
    target: { bandId: 41, songId: 7 }, targetLabel: '곡 #7', status: 'processing_failed', mediaId: 99,
    retryUnavailable: true, error: '밴드 범위 API를 지원하지 않습니다.',
  };
  const context = { tasks: [task], cancel: () => {}, retryTask: async () => assert.fail('retry must not be offered'), clearHistory: () => {} };
  const view = render(createElement(MemoryRouter, null,
    createElement(uploadContext.UploadContext.Provider, { value: context }, createElement(upload.default)),
  ));
  await userEvent.click(view.getByRole('button', { name: /업로드함/ }));
  await view.findByText('밴드 범위 API를 지원하지 않습니다.');
  assert.equal(view.queryByRole('button', { name: '재시도' }), null);
});
