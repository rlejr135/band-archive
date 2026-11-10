import assert from 'node:assert/strict';
import test from 'node:test';
import { bandUiPaths, uploadTargetPath } from './bandUiPaths.js';

test('browser detail paths match the query routes and preserve original upload scope', () => {
  assert.equal(bandUiPaths.song('first band', 7), '/bands/first%20band/songs?song=7');
  assert.equal(bandUiPaths.rehearsal('first', '9'), '/bands/first/rehearsals?rehearsal=9');
  assert.equal(bandUiPaths.member('first', 3), '/bands/first/members/3');
  assert.equal(uploadTargetPath({ bandSlug: 'original', target: { songId: 7 } }), '/bands/original/songs?song=7');
  assert.equal(uploadTargetPath({ bandSlug: 'original', target: { rehearsalId: 9 } }), '/bands/original/rehearsals?rehearsal=9');
});

test('missing origin slug or an invalid target cannot create a fallback destination', () => {
  for (const slug of [undefined, null, '', ' ']) assert.equal(bandUiPaths.song(slug, 1), null);
  for (const id of [undefined, null, '', 0, -1, 1.5, '01', '1x', true, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(bandUiPaths.song('first', id), null);
    assert.equal(bandUiPaths.rehearsal('first', id), null);
    assert.equal(bandUiPaths.member('first', id), null);
  }
  assert.equal(uploadTargetPath({ target: { songId: 7 } }), null);
  assert.equal(uploadTargetPath({ bandSlug: 'first', target: {} }), null);
});
