import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createWorkspaceRequestGate, mergeMediaPage, takeOneStaleRefresh, WorkspacePaintGate, workspaceIdentityKey } from './workspaceRouteState.js';

test('route paint gate synchronously hides A content under a requested B URL', () => {
  const html = renderToStaticMarkup(createElement(WorkspacePaintGate, {
    requestedSlug: 'band-b', resolvedSlug: 'band-a',
  }, createElement('h1', null, 'Band A confidential header')));
  assert.match(html, /data-workspace-state="loading"/);
  assert.doesNotMatch(html, /Band A confidential header/);
});

test('media pages retain more than 20 results and each expired URL refreshes once', () => {
  const first = { offset: 0, media: Array.from({ length: 20 }, (_, id) => ({ id })) };
  const next = { offset: 20, media: [{ id: 20 }, { id: 21 }] };
  const pages = mergeMediaPage([first], next);
  assert.equal(pages.flatMap((page) => page.media).length, 22);
  const attempts = new Set();
  assert.equal(takeOneStaleRefresh(attempts, 20, 21), true);
  assert.equal(takeOneStaleRefresh(attempts, 20, 21), false);
});

test('route generation rejects a late A response after B begins', () => {
  const gate = createWorkspaceRequestGate();
  const aKey = workspaceIdentityKey('band-a', 'account-a');
  const bKey = workspaceIdentityKey('band-b', 'account-a');
  const a = gate.begin(aKey);
  const b = gate.begin(bKey);
  assert.equal(gate.accepts(aKey, a), false);
  assert.equal(gate.accepts(bKey, b), true);
});
