import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('suggestions feature is independent of the workspace implementation and keeps its public route export', async () => {
  const [suggestions, workspace, routes] = await Promise.all([
    readFile(new URL('./suggestions/SuggestionsWorkspace.jsx', import.meta.url), 'utf8'),
    readFile(new URL('./BandWorkspace.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../../App.jsx', import.meta.url), 'utf8'),
  ]);
  assert.doesNotMatch(suggestions, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
  assert.match(suggestions, /from '\.\.\/WorkspaceContext\.jsx'/);
  assert.match(suggestions, /from '\.\.\/usePagedRead\.js'/);
  assert.match(suggestions, /from '\.\.\/WorkspaceWriteGuidance\.jsx'/);
  assert.match(suggestions, /import '\.\.\/BandWorkspace\.css'/);
  assert.match(suggestions, /suggestion\.can_edit/);
  assert.match(suggestions, /suggestion\.can_delete/);
  assert.match(suggestions, /suggestion\.can_proceed/);
  assert.match(suggestions, /votePayload\(suggestion\.viewer_vote === clicked \? 0 : clicked\)/);
  assert.match(suggestions, /suggestionProceed\(band\.id, suggestion\.id\)/);
  assert.match(workspace, /import \{ SuggestionsWorkspace \} from '\.\/suggestions\/SuggestionsWorkspace\.jsx'/);
  assert.match(workspace, /export \{ SuggestionsWorkspace \}/);
  assert.doesNotMatch(workspace, /export const SuggestionsWorkspace = |const SuggestionForm = |const SuggestionCard = /);
  assert.match(routes, /SuggestionsWorkspace \} from '\.\/components\/workspace\/BandWorkspace\.jsx'/);
});
