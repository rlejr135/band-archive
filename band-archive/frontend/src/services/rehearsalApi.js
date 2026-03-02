import { API_URL } from './api';

export const fetchRehearsals = async (year, month) => {
  const params = year && month ? `?year=${year}&month=${month}` : '';
  const response = await fetch(`${API_URL}/rehearsals${params}`);
  if (!response.ok) throw new Error('Failed to fetch rehearsals');
  return await response.json();
};

export const getRehearsal = async (id) => {
  const response = await fetch(`${API_URL}/rehearsals/${id}`);
  if (!response.ok) throw new Error('Failed to fetch rehearsal');
  return await response.json();
};

export const createRehearsal = async (data) => {
  const response = await fetch(`${API_URL}/rehearsals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to create rehearsal');
  return await response.json();
};

export const updateRehearsal = async (id, data) => {
  const response = await fetch(`${API_URL}/rehearsals/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to update rehearsal');
  return await response.json();
};

export const deleteRehearsal = async (id) => {
  const response = await fetch(`${API_URL}/rehearsals/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete rehearsal');
  return await response.json();
};

// Fetch media linked to a rehearsal
export const fetchRehearsalMedia = async (rehearsalId) => {
  const response = await fetch(`${API_URL}/rehearsals/${rehearsalId}/media`);
  if (!response.ok) throw new Error('Failed to fetch rehearsal media');
  return await response.json();
};

// Upload media from rehearsal (XHR with progress)
export const uploadRehearsalMedia = async (rehearsalId, songId, file, onProgress) => {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('song_id', songId);

    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) onProgress((e.loaded / e.total) * 100);
    });
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (e) {
          reject(new Error('Invalid response format'));
        }
      } else {
        let message = `Upload failed: ${xhr.statusText}`;
        try {
          const errorData = JSON.parse(xhr.responseText);
          if (errorData.error) message = errorData.error;
        } catch (e) { /* use default */ }
        reject(new Error(message));
      }
    });
    xhr.addEventListener('error', () => reject(new Error('Upload failed')));
    xhr.open('POST', `${API_URL}/rehearsals/${rehearsalId}/media`);
    xhr.send(formData);
  });
};
