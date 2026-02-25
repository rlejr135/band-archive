export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000';

// Fetch all songs
export const fetchSongs = async () => {
  const response = await fetch(`${API_URL}/songs`);
  if (!response.ok) throw new Error('Failed to fetch songs');
  return await response.json();
};

// Get single song
export const getSong = async (id) => {
  const response = await fetch(`${API_URL}/songs/${id}`);
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
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(songData),
  });
  if (!response.ok) throw new Error('Failed to update song');
  return await response.json();
};

// Delete song
export const deleteSong = async (id) => {
  const response = await fetch(`${API_URL}/songs/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete song');
  return await response.json();
};

// Upload media with progress tracking
export const uploadMedia = async (songId, file, onProgress) => {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append('file', file);

    const xhr = new XMLHttpRequest();

    // Track upload progress
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) {
        const percentComplete = (e.loaded / e.total) * 100;
        onProgress(percentComplete);
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const response = JSON.parse(xhr.responseText);
          resolve(response);
        } catch (e) {
          reject(new Error('Invalid response format'));
        }
      } else {
        reject(new Error(`Upload failed: ${xhr.statusText}`));
      }
    });

    xhr.addEventListener('error', () => {
      reject(new Error('Upload failed'));
    });

    xhr.open('POST', `${API_URL}/songs/${songId}/media`);
    xhr.send(formData);
  });
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
