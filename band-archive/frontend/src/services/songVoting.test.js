import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createSongReadRequest, createSongVoteRequest } from './api.js';
import { replaceSongAndSort, sortSongsByScore, toggleSongVote, voteStatePending, voteStateSettled } from './songVoting.js';
import { VOTER_ID_STORAGE_KEY, getVoterId } from './voterIdentity.js';

const validId = '0f2f3e0d-5c5e-4fd6-9a87-16b93b7a2631';

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
  const request = createSongVoteRequest(42, -1, validId);
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
  assert.equal(request.options.body, JSON.stringify({ vote: -1 }));
});

test('vote failure state settles without replacing the current song collection', () => {
  const songs = [{ id: 1, vote_score: 2, viewer_vote: 1 }];
  const pending = voteStatePending({}, 1);
  const failed = voteStateSettled(pending, 1, '투표를 저장하지 못했습니다. 다시 시도하세요.');
  assert.deepEqual(songs, [{ id: 1, vote_score: 2, viewer_vote: 1 }]);
  assert.deepEqual(failed[1], { loading: false, error: '투표를 저장하지 못했습니다. 다시 시도하세요.' });
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
