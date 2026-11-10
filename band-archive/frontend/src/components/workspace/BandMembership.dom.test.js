import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { JSDOM } from 'jsdom';
import { cleanup, render, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
let vite;
after(async () => { cleanup(); await vite?.close(); dom.window.close(); });

test('current write, upload, edit, comment and vote surfaces agree on membership activity', async () => {
  vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  const load = (path) => vite.ssrLoadModule(`/src/${path}`);
  const workspace = await load('components/workspace/WorkspaceContext.jsx');
  const auth = await load('context/AuthContext.jsx');
  const uploads = await load('context/UploadContext.jsx');
  const songs = await load('components/workspace/songs/SongWorkspace.jsx');
  const rehearsals = await load('components/workspace/rehearsals/RehearsalWorkspace.jsx');
  const members = await load('components/workspace/members/MembersWorkspace.jsx');
  const gallery = await load('components/workspace/gallery/GalleryWorkspace.jsx');
  const suggestions = await load('components/workspace/suggestions/SuggestionsWorkspace.jsx');
  const engagement = await load('components/workspace/MediaEngagement.jsx');
  const api = { requestJson: async () => ({ songs: [], rehearsals: [], members: [], gallery: [], suggestions: [], comments: [], pagination: { has_more: false } }) };
  const uploadValue = { nativePending: [], subscribeMediaCompletion: () => () => {} };
  for (const [membership, active] of [[{}, true], [{ status: 'active' }, true], [{ status: 'ACTIVE' }, true], ...['pending', 'left', 'rejected', null, '', 'unknown'].map((status) => [{ status }, false]), [null, false]]) {
    const band = { id: 1, slug: 'test-band', membership };
    const view = render(createElement(MemoryRouter, null, createElement(auth.AuthContext.Provider, { value: { user: { id: 'tester' } } },
      createElement(uploads.UploadContext.Provider, { value: uploadValue }, createElement(workspace.WorkspaceContext.Provider, { value: { band, api, workspaceKey: 'test:1' } },
        createElement(songs.SongWorkspace), createElement(rehearsals.RehearsalWorkspace), createElement(members.MembersWorkspace),
        createElement(gallery.GalleryWorkspace), createElement(suggestions.SuggestionsWorkspace),
        createElement(engagement.MediaEngagement, { band, api, workspaceKey: 'test:1', media: { id: 7 } }))))));
    try {
      await waitFor(() => assert.equal(view.queryByText('미디어 댓글을 불러오는 중…') === null, true));
      for (const name of ['곡 추가', '합주 추가', '내 프로필 수정', '추천곡 추가', '추천', '댓글 등록']) {
        assert.equal(view.queryByRole('button', { name, exact: true }) !== null, active, `${JSON.stringify(membership)}: ${name}`);
      }
      assert.equal(view.container.querySelector('input[type="file"]') !== null, active, 'gallery upload');
    } finally { view.unmount(); }
  }
});
