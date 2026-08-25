import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createMediaVoteRequest, createSongReadRequest, updateSong, voteMedia } from './api.js';
import { createMediaVoteChannel, isMediaVoteChannelMessage } from './mediaVoteChannel.js';
import { replaceMediaInSong, replaceMediaInSongs, sortMediaByScore, sortSongMediaByScore, toggleMediaVote, voteStatePending, voteStateSettled } from './mediaVoting.js';
import { fetchRehearsalMedia } from './rehearsalApi.js';
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

test('voter identity persists a valid UUID and never exposes it through vote data', () => {
  const storage = memoryStorage({ [VOTER_ID_STORAGE_KEY]: 'not-a-uuid' });
  const cryptoApi = { randomUUID: () => validId };
  assert.equal(getVoterId({ storage, cryptoApi }), validId);
  assert.equal(storage.value(VOTER_ID_STORAGE_KEY), validId);
});

test('media vote toggles cancel an active vote and switches direction', () => {
  assert.equal(toggleMediaVote(0, 1), 1);
  assert.equal(toggleMediaVote(1, 1), 0);
  assert.equal(toggleMediaVote(1, -1), -1);
  assert.equal(toggleMediaVote(-1, -1), 0);
});

test('media ordering is score-descending and preserves existing order for score ties', () => {
  const media = [
    { id: 4, vote_score: 3 },
    { id: 2, vote_score: 3 },
    { id: 1, vote_score: 7 },
  ];
  assert.deepEqual(sortMediaByScore(media).map((item) => item.id), [1, 4, 2]);
  assert.deepEqual(media.map((item) => item.id), [4, 2, 1]);
  assert.deepEqual(sortSongMediaByScore({ id: 5, media }).media.map((item) => item.id), [1, 4, 2]);
});

test('media vote request uses the media endpoint, expected vote, and opaque header', () => {
  const read = createSongReadRequest('/songs/7', validId);
  const request = createMediaVoteRequest(42, -1, 1, validId);
  assert.match(read.url, /\/songs\/7$/);
  assert.deepEqual(read.options.headers, { 'X-Voter-ID': validId });
  assert.match(request.url, /\/media\/42\/vote$/);
  assert.equal(request.options.method, 'PATCH');
  assert.deepEqual(request.options.headers, { 'Content-Type': 'application/json', 'X-Voter-ID': validId });
  assert.equal(request.options.body, JSON.stringify({ vote: -1, expected_viewer_vote: 1 }));
});

test('song updates and rehearsal media reads retain the voter header', async () => {
  const previousFetch = globalThis.fetch;
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: memoryStorage({ [VOTER_ID_STORAGE_KEY]: validId }) });
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ id: 7, media: [] }) };
  };
  try {
    await updateSong(7, { title: '수정한 곡' });
    await fetchRehearsalMedia(3);
    assert.deepEqual(requests[0].options.headers, {
      'Content-Type': 'application/json',
      'X-Voter-ID': validId,
    });
    assert.deepEqual(requests[1].options.headers, { 'X-Voter-ID': validId });
    assert.match(requests[1].url, /\/rehearsals\/3\/media$/);
  } finally {
    globalThis.fetch = previousFetch;
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage);
    else delete globalThis.localStorage;
  }
});

test('media vote accepts a future envelope and keeps a 409 conflict payload', async () => {
  const previousFetch = globalThis.fetch;
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: memoryStorage({ [VOTER_ID_STORAGE_KEY]: validId }) });
  try {
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ media: voteSnapshot }) });
    assert.deepEqual(await voteMedia(42, 1, 0), voteSnapshot);
    globalThis.fetch = async () => ({ ok: false, status: 409, json: async () => ({ code: 'vote_conflict', media: voteSnapshot }) });
    await assert.rejects(voteMedia(42, 0, 1), (error) => error.status === 409 && error.payload?.media?.id === 42);
  } finally {
    globalThis.fetch = previousFetch;
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage);
    else delete globalThis.localStorage;
  }
});

test('media replacement affects only its owner, keeps song order, and sorts owner media', () => {
  const songs = [{
    id: 7,
    media: [
      { id: 10, upvote_count: 0, downvote_count: 0, vote_score: 2, viewer_vote: 0 },
      voteSnapshot,
      { id: 9, upvote_count: 1, downvote_count: 1, vote_score: 0, viewer_vote: 0 },
    ],
  }, { id: 2, media: [{ id: 8 }] }];
  const updated = { ...voteSnapshot, vote_score: 5 };
  const next = replaceMediaInSongs(songs, updated);
  assert.deepEqual(next.map((song) => song.id), [7, 2]);
  assert.deepEqual(next[0].media.map((media) => media.id), [42, 10, 9]);
  assert.equal(next[0].media[0].vote_score, 5);
  assert.strictEqual(replaceMediaInSong(songs[1], updated), songs[1]);
  assert.deepEqual(voteStateSettled(voteStatePending({}, 42), 42, '실패')[42], { loading: false, error: '실패' });
});

class FakeBroadcastChannel {
  static instances = [];
  constructor(name) { this.name = name; this.listeners = new Set(); this.messages = []; FakeBroadcastChannel.instances.push(this); }
  addEventListener(_type, listener) { this.listeners.add(listener); }
  removeEventListener(_type, listener) { this.listeners.delete(listener); }
  postMessage(message) { this.messages.push(message); }
  emit(data) { for (const listener of this.listeners) listener({ data }); }
  close() {}
}

test('media channel only broadcasts media snapshots, never voter identity', () => {
  const channel = createMediaVoteChannel({ BroadcastChannelImpl: FakeBroadcastChannel });
  const implementation = FakeBroadcastChannel.instances.at(-1);
  const received = [];
  const unsubscribe = channel.subscribe((media) => received.push(media));
  channel.publish(voteSnapshot);
  assert.deepEqual(implementation.messages, [{ type: 'media-updated', media: voteSnapshot }]);
  assert.equal(JSON.stringify(implementation.messages).includes(validId), false);
  implementation.emit(implementation.messages[0]);
  assert.equal(received.length, 1);
  assert.equal(isMediaVoteChannelMessage(implementation.messages[0]), true);
  unsubscribe();
});

test('song list has no song vote controls while media UI retains accessible controls', async () => {
  const [list, detail, listCss, mediaCss, context] = await Promise.all([
    readFile(new URL('../components/songs/SongList.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/songs/SongDetail.jsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/songs/SongList.css', import.meta.url), 'utf8'),
    readFile(new URL('../components/songs/SongMedia.css', import.meta.url), 'utf8'),
    readFile(new URL('../context/SongContext.jsx', import.meta.url), 'utf8'),
  ]);
  assert.doesNotMatch(list, /song-vote|onVoteSong|vote_score/);
  assert.doesNotMatch(listCss, /song-vote/);
  assert.match(detail, /sortMediaByScore\(song\.media\)/);
  assert.match(detail, /aria-pressed=/);
  assert.match(detail, /event\.stopPropagation\(\)/);
  assert.match(mediaCss, /min-width: 44px/);
  assert.match(mediaCss, /min-height: 44px/);
  assert.match(context, /voteMedia\(mediaId, nextVote, expectedVote\)/);
  assert.match(context, /error\?\.status === 409/);
  assert.doesNotMatch(context, /voteForSong|sortSongsByScore/);
});
