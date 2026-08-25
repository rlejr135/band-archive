export const normalizeSongVote = (value) => (value === 1 || value === -1 ? value : 0);

export const toggleSongVote = (currentVote, requestedVote) => {
  const requested = normalizeSongVote(requestedVote);
  return normalizeSongVote(currentVote) === requested ? 0 : requested;
};

const numeric = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);

export const compareSongsByScore = (left, right) => {
  const scoreDelta = numeric(right?.vote_score) - numeric(left?.vote_score);
  if (scoreDelta !== 0) return scoreDelta;
  return numeric(left?.id) - numeric(right?.id);
};

export const sortSongsByScore = (songs) => [...songs].sort(compareSongsByScore);

export const replaceSongAndSort = (songs, updatedSong) => sortSongsByScore(
  songs.map((song) => (song.id === updatedSong.id ? updatedSong : song)),
);

export const isSongVoteSnapshot = (song) => (
  song
  && Number.isInteger(Number(song.id)) && Number(song.id) > 0
  && Number.isInteger(Number(song.upvote_count)) && Number(song.upvote_count) >= 0
  && Number.isInteger(Number(song.downvote_count)) && Number(song.downvote_count) >= 0
  && Number.isInteger(Number(song.vote_score))
  && normalizeSongVote(song.viewer_vote) === Number(song.viewer_vote)
);

export const sameSongVoteSnapshot = (left, right) => (
  left?.id === right?.id
  && Number(left?.upvote_count) === Number(right?.upvote_count)
  && Number(left?.downvote_count) === Number(right?.downvote_count)
  && Number(left?.vote_score) === Number(right?.vote_score)
  && normalizeSongVote(left?.viewer_vote) === normalizeSongVote(right?.viewer_vote)
);

export const replaceVoteSongAndSort = (songs, updatedSong) => {
  const existing = songs.find((song) => song.id === updatedSong?.id);
  if (!existing || sameSongVoteSnapshot(existing, updatedSong)) return songs;
  return replaceSongAndSort(songs, updatedSong);
};

export const voteStatePending = (states, songId) => ({
  ...states,
  [songId]: { loading: true, error: null },
});

export const voteStateSettled = (states, songId, error = null) => ({
  ...states,
  [songId]: { loading: false, error },
});
