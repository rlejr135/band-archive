import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');

test('workspace context is a leaf and the shell keeps the existing provider and route identity boundary', async () => {
  const [context, workspace] = await Promise.all([
    read('./WorkspaceContext.jsx'),
    read('./BandWorkspace.jsx'),
  ]);
  assert.match(context, /export const WorkspaceContext = createContext\(null\)/);
  assert.match(context, /export const useWorkspace = \(\) =>/);
  assert.match(workspace, /<WorkspaceContext\.Provider value=\{value\}>/);
  assert.match(workspace, /key=\{`\$\{workspaceIdentityKey\(bandSlug, user\?\.id\)\}:\$\{membershipRevisions\[bandSlug\] \|\| 0\}`\}/);
  assert.match(workspace, /import \{ WorkspaceContext, useWorkspace \} from '\.\/WorkspaceContext\.jsx'/);
});

test('workspace hooks are leaf modules and preserve their distinct pagination contracts', async () => {
  const [workspace, songs, rehearsals, collection, pagedRead, support, members, detail] = await Promise.all([
    read('./BandWorkspace.jsx'),
    read('./songs/SongWorkspace.jsx'),
    read('./rehearsals/RehearsalWorkspace.jsx'),
    read('./useCollection.js'),
    read('./usePagedRead.js'),
    read('./workspaceReadSupport.js'),
    read('./members/MembersWorkspace.jsx'),
    read('./members/MemberDetailWorkspace.jsx'),
  ]);
  for (const source of [collection, pagedRead]) assert.doesNotMatch(source, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
  for (const source of [songs, rehearsals]) assert.match(source, /import \{ useCollection \} from '\.\.\/useCollection\.js'/);
  assert.doesNotMatch(workspace, /import \{ useCollection \} from '\.\/useCollection\.js'/);
  for (const source of [members, detail]) assert.match(source, /import \{ usePagedRead \} from '\.\.\/usePagedRead\.js'/);
  for (const source of [workspace, songs, rehearsals]) assert.doesNotMatch(source, /const useCollection = |const usePagedRead = /);
  assert.match(collection, /append \? \[\.\.\.previous\.entries, \.\.\.next\] : next/);
  assert.match(collection, /if \(token !== version\.current\) return/);
  assert.doesNotMatch(collection, /dedupeEntries/);
  assert.match(pagedRead, /controller\.signal\.aborted \|\| !gate\.current\.accepts/);
  assert.match(pagedRead, /dedupeEntries\(append \? \[\.\.\.previous\.entries, \.\.\.next\] : next\)/);
  assert.match(pagedRead, /error\?\.status === 404/);
  assert.match(support, /PAGE_SIZE = 20/);
  assert.match(support, /authenticated: false, optionalAuth: true, signal/);
});

test('write guidance is independent of the workspace implementation', async () => {
  const [guidance, workspace, songs, rehearsals] = await Promise.all([
    read('./WorkspaceWriteGuidance.jsx'),
    read('./BandWorkspace.jsx'),
    read('./songs/SongWorkspace.jsx'),
    read('./rehearsals/RehearsalWorkspace.jsx'),
  ]);
  assert.doesNotMatch(guidance, /from ['"][^'"]*BandWorkspace\.jsx['"]|from ['"][^'"]*WorkspaceWriteGuidance\.jsx['"]/);
  assert.match(guidance, /useWorkspace\(\)/);
  assert.match(guidance, /navigate\('\/login'\)/);
  for (const source of [songs, rehearsals]) assert.match(source, /import \{ WriteGuidance \} from '\.\.\/WorkspaceWriteGuidance\.jsx'/);
  for (const source of [workspace, songs, rehearsals]) assert.doesNotMatch(source, /const WriteGuidance = /);
});
