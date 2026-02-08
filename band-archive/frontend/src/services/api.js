export const API_URL = 'http://localhost:5000';

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

    xhr.open('POST', `${API_URL}/songs/${songId}/upload`);
    xhr.send(formData);
  });
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
