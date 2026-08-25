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

export const voteStatePending = (states, songId) => ({
  ...states,
  [songId]: { loading: true, error: null },
});

export const voteStateSettled = (states, songId, error = null) => ({
  ...states,
  [songId]: { loading: false, error },
});
