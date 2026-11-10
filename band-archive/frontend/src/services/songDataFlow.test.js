import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import test from 'node:test';

const retiredPaths = [
  'components/calendar/LocationPicker.jsx',
  'components/calendar/RehearsalCalendar.jsx',
  'components/calendar/RehearsalDetail.jsx',
  'components/calendar/RehearsalModal.jsx',
  'components/common/AnnouncementToast.jsx',
  'components/common/CommentSection.jsx',
  'components/common/MediaDiscussion.jsx',
  'components/common/PasswordModal.jsx',
  'components/common/SearchBar.jsx',
  'components/dashboard/Dashboard.jsx',
  'components/gallery/Gallery.jsx',
  'components/layout/AuthStatus.jsx',
  'components/members/MemberDashboard.jsx',
  'components/members/MemberDetail.jsx',
  'components/songs/SongDetail.jsx',
  'components/songs/SongForm.jsx',
  'components/songs/SongList.jsx',
  'components/songs/SongSuggestion.jsx',
  'context/SongContext.jsx',
  'hooks/useAsyncData.js',
  'services/api.js',
  'services/commentRequestGuard.js',
  'services/galleryApi.js',
  'services/mediaVoteChannel.js',
  'services/mediaVoting.js',
  'services/memberApi.js',
  'services/rehearsalApi.js',
  'services/rehearsalUploadQueue.js',
  'services/voterIdentity.js',
  'services/youtube.js',
];

test('retired unscoped screens and their exclusive helpers cannot reenter the frontend', async () => {
  for (const path of retiredPaths) {
    await assert.rejects(stat(new URL(`../${path}`, import.meta.url)), { code: 'ENOENT' }, path);
  }
  await assert.rejects(stat(new URL('../components/common/SearchBar.stories.jsx', import.meta.url)), { code: 'ENOENT' });
});

test('browser routes connect directly to scoped song and rehearsal workspaces', async () => {
  const app = await readFile(new URL('../App.jsx', import.meta.url), 'utf8');
  assert.doesNotMatch(app, /SongProvider|LegacyApp|SongContext|\/dashboard\/Dashboard\.jsx|\/songs\/SongList\.jsx/);
  assert.match(app, /from ['"]\.\/components\/workspace\/songs\/SongWorkspace\.jsx['"]/);
  assert.match(app, /from ['"]\.\/components\/workspace\/rehearsals\/RehearsalWorkspace\.jsx['"]/);
  assert.match(app, /path="\/bands\/:bandSlug"/);
  assert.match(app, /path="songs" element=\{<SongWorkspace \/>\}/);
  assert.match(app, /path="rehearsals" element=\{<RehearsalWorkspace \/>\}/);
  // LegacyRoute is the supported URL alias handler, not a retired screen.
  assert.match(app, /path="\*" element=\{<LegacyRoute \/>\}/);
});
