import assert from 'node:assert/strict';
import test from 'node:test';
import { bandWorkspacePaths, commentPayload, rehearsalPayload, songPayload, votePayload } from './bandWorkspaceApi.js';

test('workspace paths remain v2, tenant-scoped, and URL-safe', () => {
  assert.equal(bandWorkspacePaths.bandBySlug('my band/2026'), '/api/v2/bands/by-slug/my%20band%2F2026');
  assert.equal(bandWorkspacePaths.dashboard(7, { month: '2026-10' }), '/api/v2/bands/7/dashboard?month=2026-10&limit=25&offset=0');
  assert.equal(bandWorkspacePaths.dashboard(7, { month: '2026-10', limit: 25, offset: 25 }), '/api/v2/bands/7/dashboard?month=2026-10&limit=25&offset=25');
  assert.equal(bandWorkspacePaths.songs(7, { limit: 20, offset: 40 }), '/api/v2/bands/7/songs?limit=20&offset=40');
  assert.equal(bandWorkspacePaths.songMedia(7, 3), '/api/v2/bands/7/songs/3/media?limit=20&offset=0');
  assert.equal(bandWorkspacePaths.rehearsalMedia(7, 4), '/api/v2/bands/7/rehearsals/4/media?limit=20&offset=0');
  assert.equal(bandWorkspacePaths.mediaVote(7, 4), '/api/v2/bands/7/media/4/vote');
  assert.equal(bandWorkspacePaths.mediaComments(7, 4, { limit: 3, offset: 9 }), '/api/v2/bands/7/media/4/comments?limit=3&offset=9');
  assert.equal(bandWorkspacePaths.mediaCommentsWrite(7, 4), '/api/v2/bands/7/media/4/comments');
  assert.equal(bandWorkspacePaths.comment(7, 4), '/api/v2/bands/7/comments/4');
});

test('write payloads never carry client-provided tenant or creator identity', () => {
  const song = songPayload({ title: '곡', artist: '밴드', visibility: 'PUBLIC', band_id: 999, created_by_membership_id: 8 });
  const rehearsal = rehearsalPayload({ title: '합주', date: '2026-09-27', song_ids: ['2', 'bad'], band_id: 999 });
  assert.equal('band_id' in song, false);
  assert.equal('created_by_membership_id' in song, false);
  assert.deepEqual(rehearsal.song_ids, [2]);
  assert.equal('band_id' in rehearsal, false);
});

test('media engagement payloads are account-owned and tenant-free', () => {
  assert.deepEqual(votePayload(-1), { value: -1 });
  assert.deepEqual(commentPayload({ content: 'reply', parentId: 22, author: 'spoof', password: 'legacy' }), { content: 'reply', parent_id: 22 });
  assert.deepEqual(commentPayload({ content: 'root' }), { content: 'root' });
});
