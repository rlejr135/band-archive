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

// Fetch all songs
export const fetchSongs = async () => {
  const { url, options } = createSongReadRequest('/songs');
  const response = await fetch(url, options);
  if (!response.ok) throw new Error('Failed to fetch songs');
  return await response.json();
};

// Get single song
export const getSong = async (id) => {
  const { url, options } = createSongReadRequest(`/songs/${id}`);
  const response = await fetch(url, options);
  if (!response.ok) throw new Error('Failed to fetch song');
  return await response.json();
};

// Create new song
export const createSong = async (songData) => {
  const response = await fetch(`${API_URL}/songs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(songData),
  });
  if (!response.ok) throw new Error('Failed to create song');
  return await response.json();
};

// Update song
export const updateSong = async (id, songData) => {
  const response = await fetch(`${API_URL}/songs/${id}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...voterHeaders(),
    },
    body: JSON.stringify(songData),
  });
  if (!response.ok) throw new Error('Failed to update song');
  return await response.json();
};

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
export const deleteSong = async (id) => {
  const response = await fetch(`${API_URL}/songs/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete song');
  return await response.json();
};

// Link/unlink media to rehearsal
export const linkMediaToRehearsal = async (mediaId, rehearsalId) => {
  const response = await fetch(`${API_URL}/media/${mediaId}/rehearsal`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rehearsal_id: rehearsalId }),
  });
  if (!response.ok) throw new Error('Failed to link media to rehearsal');
  return await response.json();
};

// Delete media
export const deleteMedia = async (mediaId) => {
  const response = await fetch(`${API_URL}/media/${mediaId}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete media');
  return await response.json();
};

// Rename media
export const renameMedia = async (mediaId, newFilename) => {
  const response = await fetch(`${API_URL}/media/${mediaId}/rename`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ filename: newFilename }),
  });
  if (!response.ok) throw new Error('Failed to rename media');
  return await response.json();
};

// Set featured media
export const setFeaturedMedia = async (mediaId) => {
  const response = await fetch(`${API_URL}/media/${mediaId}/featured`, { method: 'PATCH' });
  if (!response.ok) throw new Error('Failed to set featured media');
  return response.json();
};

// Dashboard stats
export const fetchDashboardStats = async () => {
  const response = await fetch(`${API_URL}/dashboard/stats`);
  if (!response.ok) throw new Error('Failed to fetch dashboard stats');
  return await response.json();
};

// Fetch all song suggestions
export const fetchSuggestions = async () => {
  const response = await fetch(`${API_URL}/suggestions`);
  if (!response.ok) throw new Error('Failed to fetch suggestions');
  return await response.json();
};

// Create song suggestion
export const createSuggestion = async (data) => {
  const response = await fetch(`${API_URL}/suggestions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to create suggestion');
  return await response.json();
};

// Delete song suggestion
export const deleteSuggestion = async (id, password) => {
  const response = await fetch(`${API_URL}/suggestions/${id}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) throw new Error('Failed to delete suggestion');
  return await response.json();
};

// Vote on song suggestion
export const voteSuggestion = async (id, voteType) => {
  const response = await fetch(`${API_URL}/suggestions/${id}/vote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ vote_type: voteType }),
  });
  if (!response.ok) throw new Error('Failed to vote');
  return await response.json();
};

// Fetch current announcement
export const fetchAnnouncement = async () => {
  const response = await fetch(`${API_URL}/announcement`);
  if (!response.ok) throw new Error('Failed to fetch announcement');
  return await response.json();
};

// Update announcement (upsert)
export const updateAnnouncement = async (content) => {
  const response = await fetch(`${API_URL}/announcement`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) throw new Error('Failed to update announcement');
  return await response.json();
};

// Fetch comments for a target (media or personal-log)
export const fetchComments = async (targetType, targetId) => {
  const response = await fetch(`${API_URL}/${targetType}/${targetId}/comments`);
  if (!response.ok) throw new Error('Failed to fetch comments');
  return await response.json();
};

// Create a comment on a target
export const createComment = async (targetType, targetId, { author, password, content }) => {
  const response = await fetch(`${API_URL}/${targetType}/${targetId}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ author, password, content }),
  });
  if (!response.ok) throw new Error('Failed to create comment');
  return await response.json();
};

// Create a reply to a comment
export const createReply = async (commentId, { author, password, content }) => {
  const response = await fetch(`${API_URL}/comments/${commentId}/replies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ author, password, content }),
  });
  if (!response.ok) throw new Error('Failed to create reply');
  return await response.json();
};

// Update a comment (password required)
export const updateComment = async (commentId, { password, content }) => {
  const response = await fetch(`${API_URL}/comments/${commentId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password, content }),
  });
  if (!response.ok) throw new Error('Failed to update comment');
  return await response.json();
};

// Delete a comment (password required)
export const deleteComment = async (commentId, password) => {
  const response = await fetch(`${API_URL}/comments/${commentId}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) throw new Error('Failed to delete comment');
  return await response.json();
};
