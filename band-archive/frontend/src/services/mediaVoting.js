export const normalizeMediaVote = (value) => (value === 1 || value === -1 ? value : 0);

export const toggleMediaVote = (currentVote, requestedVote) => {
  const requested = normalizeMediaVote(requestedVote);
  return normalizeMediaVote(currentVote) === requested ? 0 : requested;
};

const numeric = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);

/** Highest-scored uploads first. Equal scores retain the server's existing order. */
export const sortMediaByScore = (media = []) => media
  .map((item, index) => ({ item, index }))
  .sort((left, right) => {
    const scoreDelta = numeric(right.item?.vote_score) - numeric(left.item?.vote_score);
    if (scoreDelta !== 0) return scoreDelta;
    return left.index - right.index || numeric(left.item?.id) - numeric(right.item?.id);
  })
  .map(({ item }) => item);

export const isMediaVoteSnapshot = (media) => (
  media
  && Number.isInteger(Number(media.id)) && Number(media.id) > 0
  && Number.isInteger(Number(media.upvote_count)) && Number(media.upvote_count) >= 0
  && Number.isInteger(Number(media.downvote_count)) && Number(media.downvote_count) >= 0
  && Number.isInteger(Number(media.vote_score))
  && normalizeMediaVote(media.viewer_vote) === Number(media.viewer_vote)
);

export const sameMediaVoteSnapshot = (left, right) => (
  left?.id === right?.id
  && Number(left?.upvote_count) === Number(right?.upvote_count)
  && Number(left?.downvote_count) === Number(right?.downvote_count)
  && Number(left?.vote_score) === Number(right?.vote_score)
  && normalizeMediaVote(left?.viewer_vote) === normalizeMediaVote(right?.viewer_vote)
);

/** Sort one song's media without ever changing the song collection's order. */
export const sortSongMediaByScore = (song) => {
  if (!Array.isArray(song?.media)) return song;
  const sortedMedia = sortMediaByScore(song.media);
  const mediaOrderChanged = sortedMedia.some((media, index) => media !== song.media[index]);
  return mediaOrderChanged ? { ...song, media: sortedMedia } : song;
};

export const replaceMediaInSong = (song, updatedMedia) => {
  if (!song?.media?.some((media) => media.id === updatedMedia?.id)) return song;
  const matchingMedia = song.media.find((media) => media.id === updatedMedia.id);
  const withSnapshot = sameMediaVoteSnapshot(matchingMedia, updatedMedia)
    ? song.media
    : song.media.map((media) => (media.id === updatedMedia.id ? { ...media, ...updatedMedia } : media));
  return sortSongMediaByScore(withSnapshot === song.media ? song : { ...song, media: withSnapshot });
};

export const replaceMediaInSongs = (songs, updatedMedia) => {
  let changed = false;
  const updated = songs.map((song) => {
    const next = replaceMediaInSong(song, updatedMedia);
    changed ||= next !== song;
    return next;
  });
  return changed ? updated : songs;
};

export const voteStatePending = (states, mediaId) => ({
  ...states,
  [mediaId]: { loading: true, error: null },
});

export const voteStateSettled = (states, mediaId, error = null) => ({
  ...states,
  [mediaId]: { loading: false, error },
});
