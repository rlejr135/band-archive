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

// Fetch practice logs for a song
export const fetchPracticeLogs = async (songId) => {
  const response = await fetch(`${API_URL}/songs/${songId}/practice-logs`);
  if (!response.ok) throw new Error('Failed to fetch practice logs');
  return await response.json();
};

// Create practice log
export const createPracticeLog = async (songId, data) => {
  const response = await fetch(`${API_URL}/songs/${songId}/practice-logs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to create practice log');
  return await response.json();
};

// Get single practice log
export const getPracticeLog = async (id) => {
  const response = await fetch(`${API_URL}/practice-logs/${id}`);
  if (!response.ok) throw new Error('Failed to fetch practice log');
  return await response.json();
};

// Update practice log
export const updatePracticeLog = async (id, data) => {
  const response = await fetch(`${API_URL}/practice-logs/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to update practice log');
  return await response.json();
};

// Delete practice log
export const deletePracticeLog = async (id) => {
  const response = await fetch(`${API_URL}/practice-logs/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete practice log');
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

// Upload recording to practice log
export const uploadRecording = async (id, file, onProgress) => {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append('file', file);

    const xhr = new XMLHttpRequest();

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

    xhr.open('POST', `${API_URL}/practice-logs/${id}/upload`);
    xhr.send(formData);
  });
};
