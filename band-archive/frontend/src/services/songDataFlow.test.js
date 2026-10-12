import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('song data helpers remain isolated from the browser router after legacy UI retirement', async () => {
  const [context, calendar, dashboard, app] = await Promise.all([
    readFile(new URL('../context/SongContext.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/calendar/RehearsalCalendar.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/dashboard/Dashboard.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../App.jsx', import.meta.url), 'utf8'),
  ]);
  assert.match(context, /songsRequestRef/);
  assert.match(context, /if \(songsRequestRef\.current\) return songsRequestRef\.current/);
  assert.match(calendar, /const \{ songs \} = useSongs\(\)/);
  assert.doesNotMatch(calendar, /fetchSongs/);
  assert.match(dashboard, /const \{ songs \} = useSongs\(\)/);
  assert.doesNotMatch(dashboard, /fetchSongs/);
  assert.doesNotMatch(app, /SongProvider|LegacyApp|SongContext|Dashboard\.jsx|SongList\.jsx/);
  assert.match(app, /path="\/bands\/:bandSlug"/);
  assert.match(app, /path="\*" element=\{<LegacyRoute \/>\}/);
});
