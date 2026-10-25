import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');

test('member screens and personal playback depend on leaf modules and App uses their direct entries', async () => {
  const [members, detail, playback, unavailable, app, shell] = await Promise.all([
    read('./members/MembersWorkspace.jsx'), read('./members/MemberDetailWorkspace.jsx'),
    read('./members/PersonalLogPlayback.jsx'), read('./members/MemberRosterUnavailable.jsx'),
    read('../../App.jsx'), read('./BandWorkspace.jsx'),
  ]);
  for (const source of [members, detail, playback, unavailable]) {
    assert.doesNotMatch(source, /from ['"][^'"]*BandWorkspace\.jsx['"]/);
    assert.doesNotMatch(source, /import ['"][^'"]*\.css['"]/);
  }
  for (const name of ['MembersWorkspace', 'MemberDetailWorkspace']) {
    assert.match(app, new RegExp(`import \\{ ${name} \\} from './components/workspace/members/${name}\\.jsx'`));
    assert.doesNotMatch(shell, new RegExp(`(?:export )?const ${name} =`));
  }
  assert.match(shell, /export \{ MembersWorkspace, MemberDetailWorkspace \}/);
  assert.match(detail, /key=\{`\$\{workspaceKey\}:member:\$\{memberId\}`\}/);
  assert.match(detail, /key=\{`\$\{workspaceKey\}:\$\{memberId\}`\}/);
});
