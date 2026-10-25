import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('featured workspace has a direct route entry and preserves standalone editor and shell compatibility boundaries', async () => {
  const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');
  const [featured, app, shell, management] = await Promise.all([
    read('./featured-media/FeaturedMediaWorkspace.jsx'), read('../../App.jsx'),
    read('./BandWorkspace.jsx'), read('../bands/RepresentativeMediaManagement.jsx'),
  ]);
  assert.doesNotMatch(featured, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
  assert.match(featured, /from '\.\.\/WorkspaceContext\.jsx'/);
  assert.match(featured, /from '\.\/FeaturedMediaEditor\.jsx'/);
  assert.match(app, /import \{ RepresentativeMediaWorkspace \} from '\.\/components\/workspace\/featured-media\/FeaturedMediaWorkspace\.jsx'/);
  assert.match(shell, /export \{ RepresentativeMediaWorkspace \}/);
  assert.doesNotMatch(shell, /const RepresentativeMediaWorkspace =|const RepresentativeMediaCard =/);
  assert.match(management, /from '\.\.\/workspace\/featured-media\/FeaturedMediaEditor\.jsx'/);
  assert.doesNotMatch(management, /FeaturedMediaWorkspace\.jsx/);
});
