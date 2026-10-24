import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('representative media management imports the standalone editor without the workspace implementation', async () => {
  const management = await readFile(new URL('../bands/RepresentativeMediaManagement.jsx', import.meta.url), 'utf8');
  assert.match(management, /import \{ FeaturedMediaEditor \} from '\.\.\/workspace\/featured-media\/FeaturedMediaEditor\.jsx'/);
  assert.doesNotMatch(management, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
});

test('standalone featured editor and its shared leaves do not depend back on the workspace implementation', async () => {
  const editor = await readFile(new URL('./featured-media/FeaturedMediaEditor.jsx', import.meta.url), 'utf8');
  const presentation = await readFile(new URL('./featured-media/featuredMediaPresentation.js', import.meta.url), 'utf8');
  const feedback = await readFile(new URL('./WorkspaceFeedback.jsx', import.meta.url), 'utf8');
  const workspace = await readFile(new URL('./BandWorkspace.jsx', import.meta.url), 'utf8');
  for (const source of [editor, presentation, feedback]) assert.doesNotMatch(source, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
  assert.match(editor, /import '\.\.\/BandWorkspace\.css'/);
  assert.match(editor, /import \{ ErrorState \} from '\.\.\/WorkspaceFeedback\.jsx'/);
  assert.match(editor, /from '\.\/featuredMediaPresentation\.js'/);
  assert.match(workspace, /from '\.\/featured-media\/featuredMediaPresentation\.js'/);
  assert.match(workspace, /import \{ ErrorState \} from '\.\/WorkspaceFeedback\.jsx'/);
  assert.match(workspace, /export \{ FeaturedMediaEditor \}/);
  assert.doesNotMatch(workspace, /export const FeaturedMediaEditor = |const mediaPlayerFile = |const mergeUniqueEntries = |const formatRepresentativeDate = |const ErrorState = /);
});
