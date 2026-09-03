import { getVoterId } from './voterIdentity.js';

export const API_URL = import.meta.env?.VITE_API_URL || 'http://localhost:5000';

export const voterHeaders = (voterId = getVoterId()) => ({
  'X-Voter-ID': voterId,
});

// Kept as an alias for callers that fetch songs with the viewer-vote header.
export const songVoterHeaders = voterHeaders;

export const createSongReadRequest = (path, voterId = getVoterId()) => ({
  url: `${API_URL}${path}`,
  options: { headers: voterHeaders(voterId) },
});

export const createMediaVoteRequest = (id, vote, expectedViewerVote, voterId = getVoterId()) => ({
  url: `${API_URL}/media/${id}/vote`,
  options: {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...voterHeaders(voterId),
    },
    body: JSON.stringify({ vote, expected_viewer_vote: expectedViewerVote }),
  },
});

// Keep ordinary JSON endpoints consistent without folding the media-vote
// conflict contract into the generic path below.
export const requestJson = async (path, options = {}, errorMessage = 'Request failed') => {
  const response = await fetch(`${API_URL}${path}`, options);
  if (!response.ok) throw new Error(errorMessage);
  return response.json();
};

export const jsonRequest = (method, body, headers = {}) => ({
  method,
  headers: { 'Content-Type': 'application/json', ...headers },
  body: JSON.stringify(body),
});

// Fetch all songs
export const fetchSongs = () => requestJson('/songs', { headers: voterHeaders() }, 'Failed to fetch songs');

// Get single song
export const getSong = (id) => requestJson(`/songs/${id}`, { headers: voterHeaders() }, 'Failed to fetch song');

// Create new song
export const createSong = (songData) => requestJson('/songs', jsonRequest('POST', songData), 'Failed to create song');

// Update song
export const updateSong = (id, songData) => requestJson(
  `/songs/${id}`,
  jsonRequest('PUT', songData, voterHeaders()),
  'Failed to update song',
);

export const voteMedia = async (id, vote, expectedViewerVote) => {
  const { url, options } = createMediaVoteRequest(id, vote, expectedViewerVote);
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // Keep a transport-level failure useful without requiring a JSON body.
  }
  if (!response.ok) {
    const error = new Error(payload?.error || 'Failed to vote for media');
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  // Accept either the media object directly or a future envelope such as { media }.
  return payload?.media || payload;
};

// Delete song
export const deleteSong = (id) => requestJson(`/songs/${id}`, { method: 'DELETE' }, 'Failed to delete song');

// Link/unlink media to rehearsal
export const linkMediaToRehearsal = (mediaId, rehearsalId) => requestJson(
  `/media/${mediaId}/rehearsal`,
  jsonRequest('PATCH', { rehearsal_id: rehearsalId }),
  'Failed to link media to rehearsal',
);

// Delete media
export const deleteMedia = (mediaId) => requestJson(`/media/${mediaId}`, { method: 'DELETE' }, 'Failed to delete media');

// Rename media
export const renameMedia = (mediaId, newFilename) => requestJson(
  `/media/${mediaId}/rename`,
  jsonRequest('PUT', { filename: newFilename }),
  'Failed to rename media',
);

// Set featured media
export const setFeaturedMedia = (mediaId) => requestJson(`/media/${mediaId}/featured`, { method: 'PATCH' }, 'Failed to set featured media');

// Dashboard stats
export const fetchDashboardStats = () => requestJson('/dashboard/stats', {}, 'Failed to fetch dashboard stats');

// Fetch all song suggestions
export const fetchSuggestions = () => requestJson('/suggestions', {}, 'Failed to fetch suggestions');

// Create song suggestion
export const createSuggestion = (data) => requestJson('/suggestions', jsonRequest('POST', data), 'Failed to create suggestion');

// Delete song suggestion
export const deleteSuggestion = (id, password) => requestJson(
  `/suggestions/${id}`,
  jsonRequest('DELETE', { password }),
  'Failed to delete suggestion',
);

export const promoteSuggestion = (id, password) => requestJson(
  `/suggestions/${id}/promote`,
  jsonRequest('POST', { password }),
  'Failed to move suggestion to songs',
);

// Vote on song suggestion
export const voteSuggestion = (id, voteType) => requestJson(
  `/suggestions/${id}/vote`,
  jsonRequest('POST', { vote_type: voteType }),
  'Failed to vote',
);

// Fetch current announcement
export const fetchAnnouncement = () => requestJson('/announcement', {}, 'Failed to fetch announcement');

// Update announcement (upsert)
export const updateAnnouncement = (content) => requestJson(
  '/announcement',
  jsonRequest('PUT', { content }),
  'Failed to update announcement',
);

// Fetch comments for a target (media or personal-log)
export const fetchComments = (targetType, targetId) => requestJson(
  `/${targetType}/${targetId}/comments`,
  {},
  'Failed to fetch comments',
);

// Create a comment on a target
export const createComment = (targetType, targetId, { author, password, content }) => requestJson(
  `/${targetType}/${targetId}/comments`,
  jsonRequest('POST', { author, password, content }),
  'Failed to create comment',
);

// Create a reply to a comment
export const createReply = (commentId, { author, password, content }) => requestJson(
  `/comments/${commentId}/replies`,
  jsonRequest('POST', { author, password, content }),
  'Failed to create reply',
);

// Update a comment (password required)
export const updateComment = (commentId, { password, content }) => requestJson(
  `/comments/${commentId}`,
  jsonRequest('PUT', { password, content }),
  'Failed to update comment',
);

// Delete a comment (password required)
export const deleteComment = (commentId, password) => requestJson(
  `/comments/${commentId}`,
  jsonRequest('DELETE', { password }),
  'Failed to delete comment',
);
