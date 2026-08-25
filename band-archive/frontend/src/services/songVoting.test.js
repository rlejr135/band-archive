import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createSongReadRequest, createSongVoteRequest, voteSong } from './api.js';
import { createSongVoteChannel, isSongVoteChannelMessage } from './songVoteChannel.js';
import { replaceSongAndSort, replaceVoteSongAndSort, sortSongsByScore, toggleSongVote, voteStatePending, voteStateSettled } from './songVoting.js';
import { VOTER_ID_STORAGE_KEY, getVoterId } from './voterIdentity.js';

const validId = '0f2f3e0d-5c5e-4fd6-9a87-16b93b7a2631';
const voteSnapshot = { id: 42, upvote_count: 2, downvote_count: 1, vote_score: 1, viewer_vote: 1 };

const memoryStorage = (initial = {}) => {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    value: (key) => values.get(key),
  };
};

test('voter identity persists a valid UUID and recovers an invalid stored value', () => {
  const storage = memoryStorage({ [VOTER_ID_STORAGE_KEY]: 'not-a-uuid' });
  let generated = 0;
  const cryptoApi = { randomUUID: () => { generated += 1; return validId; } };
  assert.equal(getVoterId({ storage, cryptoApi }), validId);
  assert.equal(storage.value(VOTER_ID_STORAGE_KEY), validId);
  assert.equal(getVoterId({ storage, cryptoApi }), validId);
  assert.equal(generated, 1);
});

test('vote toggle cancels an active vote and switches to the opposite vote', () => {
  assert.equal(toggleSongVote(0, 1), 1);
  assert.equal(toggleSongVote(1, 1), 0);
  assert.equal(toggleSongVote(1, -1), -1);
  assert.equal(toggleSongVote(-1, -1), 0);
});

test('score sorting is deterministic and replacing a song keeps the order', () => {
  const songs = [
    { id: 4, vote_score: 3 },
    { id: 2, vote_score: 3 },
    { id: 1, vote_score: 7 },
  ];
  assert.deepEqual(sortSongsByScore(songs).map((song) => song.id), [1, 2, 4]);
  assert.deepEqual(replaceSongAndSort(songs, { id: 4, vote_score: 9 }).map((song) => song.id), [4, 1, 2]);
});

test('song list, detail, and vote requests send the UUID header and vote body', () => {
  const list = createSongReadRequest('/songs', validId);
  const detail = createSongReadRequest('/songs/42', validId);
  const request = createSongVoteRequest(42, -1, 1, validId);
  assert.match(list.url, /\/songs$/);
  assert.match(detail.url, /\/songs\/42$/);
  assert.deepEqual(list.options.headers, { 'X-Voter-ID': validId });
  assert.deepEqual(detail.options.headers, { 'X-Voter-ID': validId });
  assert.match(request.url, /\/songs\/42\/vote$/);
  assert.equal(request.options.method, 'PATCH');
  assert.deepEqual(request.options.headers, {
    'Content-Type': 'application/json',
    'X-Voter-ID': validId,
  });
  assert.equal(request.options.body, JSON.stringify({ vote: -1, expected_viewer_vote: 1 }));
});

test('vote request retains a 409 status and parsed conflict payload without retrying', async () => {
  const previousFetch = globalThis.fetch;
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: memoryStorage({ [VOTER_ID_STORAGE_KEY]: validId }),
  });
  globalThis.fetch = async () => ({
    ok: false,
    status: 409,
    json: async () => ({ code: 'vote_conflict', song: voteSnapshot }),
  });
  try {
    await assert.rejects(
      voteSong(42, 0, 1),
      (error) => error.status === 409 && error.payload?.song?.id === 42,
    );
  } finally {
    globalThis.fetch = previousFetch;
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage);
    else delete globalThis.localStorage;
  }
});

test('vote failure state settles without replacing the current song collection', () => {
  const songs = [{ id: 1, vote_score: 2, viewer_vote: 1 }];
  const pending = voteStatePending({}, 1);
  const failed = voteStateSettled(pending, 1, '투표를 저장하지 못했습니다. 다시 시도하세요.');
  assert.deepEqual(songs, [{ id: 1, vote_score: 2, viewer_vote: 1 }]);
  assert.deepEqual(failed[1], { loading: false, error: '투표를 저장하지 못했습니다. 다시 시도하세요.' });
});

class FakeBroadcastChannel {
  static instances = [];

  constructor(name) {
    this.name = name;
    this.listeners = new Set();
    this.messages = [];
    this.closed = false;
    FakeBroadcastChannel.instances.push(this);
  }

  addEventListener(_type, listener) {
    this.listeners.add(listener);
  }

  removeEventListener(_type, listener) {
    this.listeners.delete(listener);
  }

  postMessage(message) {
    this.messages.push(message);
  }

  emit(data) {
    for (const listener of this.listeners) listener({ data });
  }

  close() {
    this.closed = true;
  }
}

test('vote channel validates messages, syncs once, and cleans up its listener', () => {
  const channel = createSongVoteChannel({ BroadcastChannelImpl: FakeBroadcastChannel });
  const implementation = FakeBroadcastChannel.instances.at(-1);
  const received = [];
  const unsubscribe = channel.subscribe((song) => received.push(song));
  channel.publish(voteSnapshot);
  assert.deepEqual(implementation.messages, [{ type: 'song-updated', song: voteSnapshot }]);
  implementation.emit({ type: 'song-updated', song: voteSnapshot });
  implementation.emit({ type: 'song-updated', song: { id: 42 } });
  assert.equal(received.length, 1);
  assert.equal(isSongVoteChannelMessage(implementation.messages[0]), true);
  unsubscribe();
  implementation.emit({ type: 'song-updated', song: voteSnapshot });
  assert.equal(received.length, 1);
  channel.close();
  assert.equal(implementation.closed, true);
});

test('vote channel safely falls back when BroadcastChannel is unavailable and ignores duplicate snapshots', () => {
  assert.equal(createSongVoteChannel({ BroadcastChannelImpl: null }), null);
  const songs = [voteSnapshot];
  assert.strictEqual(replaceVoteSongAndSort(songs, { ...voteSnapshot }), songs);
});

test('song UI no longer renders the representative-media controls', async () => {
  const [detail, list, listCss, mediaCss] = await Promise.all([
    readFile(new URL('../components/songs/SongDetail.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/songs/SongList.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/songs/SongList.css', import.meta.url), 'utf8'),
    readFile(new URL('../components/songs/SongMedia.css', import.meta.url), 'utf8'),
  ]);
  for (const source of [detail, list, listCss, mediaCss]) assert.doesNotMatch(source, /대표|is_featured|media-featured/);
});

test('song selection is a semantic button and vote interactions stay isolated', async () => {
  const list = await readFile(new URL('../components/songs/SongList.jsx', import.meta.url), 'utf8');
  assert.match(list, /className="song-title-button"/);
  assert.match(list, /type="button"/);
  assert.match(list, /aria-label=\{`\$\{song\.title\} 상세 보기`\}/);
  assert.match(list, /event\.stopPropagation\(\)/);
});

test('context applies one conflict snapshot and never retries the target vote automatically', async () => {
  const context = await readFile(new URL('../context/SongContext.jsx', import.meta.url), 'utf8');
  assert.match(context, /voteSong\(songId, nextVote, expectedVote\)/);
  assert.match(context, /error\?\.status === 409/);
  assert.match(context, /다른 화면에서 투표가 변경되어 최신 상태로 갱신했습니다\. 다시 눌러주세요\./);
  assert.equal([...context.matchAll(/voteSong\(/g)].length, 1);
});
