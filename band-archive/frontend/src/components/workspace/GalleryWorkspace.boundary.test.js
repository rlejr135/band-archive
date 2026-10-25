import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('gallery feature and upload input are leaf modules with the same provider boundary', async () => {
  const [gallery, uploadInput, songDetail, workspace, routes] = await Promise.all([
    readFile(new URL('./gallery/GalleryWorkspace.jsx', import.meta.url), 'utf8'),
    readFile(new URL('./BandUploadInput.jsx', import.meta.url), 'utf8'),
    readFile(new URL('./songs/SongDetail.jsx', import.meta.url), 'utf8'),
    readFile(new URL('./BandWorkspace.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../../App.jsx', import.meta.url), 'utf8'),
  ]);
  assert.doesNotMatch(gallery, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
  assert.match(gallery, /from '\.\.\/WorkspaceContext\.jsx'/);
  assert.match(gallery, /from '\.\.\/usePagedRead\.js'/);
  assert.match(gallery, /from '\.\.\/BandUploadInput\.jsx'/);
  assert.match(gallery, /import '\.\.\/BandWorkspace\.css'/);
  assert.match(gallery, /uploadGate\.current\.accepts\(requestKey, token\)/);
  assert.match(gallery, /reviewIsCurrent\(\) && uploadGate\.current\.accepts/);
  assert.match(gallery, /setFeatured\(null\)/);
  assert.match(gallery, /takeOneStaleRefresh/);
  assert.match(gallery, /canDelete=\{image\.can_delete\}/);
  assert.match(uploadInput, /useContext\(UploadContext\) \? <FileUpload \{\.\.\.props\} \/> : null/);
  assert.match(workspace, /import \{ GalleryWorkspace \} from '\.\/gallery\/GalleryWorkspace\.jsx'/);
  assert.match(songDetail, /import \{ BandUploadInput \} from '\.\.\/BandUploadInput\.jsx'/);
  assert.match(songDetail, /parentVisibility=\{item\.effective_visibility \|\| item\.visibility\}/);
  assert.match(songDetail, /onMediaComplete=\{refreshAuthoritativeMedia\}/);
  assert.match(workspace, /export \{ GalleryWorkspace \}/);
  assert.doesNotMatch(workspace, /export const GalleryWorkspace = |const BandUploadInput = /);
  assert.match(routes, /import \{[^}]*\bGalleryWorkspace\b[^}]*\} from '\.\/components\/workspace\/BandWorkspace\.jsx'/);
});
