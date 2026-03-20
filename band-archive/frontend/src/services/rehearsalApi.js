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

// Upload media from rehearsal (presigned URL → R2 직접 업로드)
export const uploadRehearsalMedia = async (rehearsalId, songId, file, onProgress) => {
  const { getPresignedUrl, uploadToStorage, completeMediaUpload } = await import('./uploadApi');

  const contentType = file.type || 'application/octet-stream';
  const { upload_url, filename } = await getPresignedUrl(file.name, contentType, 'media');
  await uploadToStorage(upload_url, file, contentType, onProgress);
  return completeMediaUpload({
    filename,
    original_filename: file.name,
    file_size: file.size,
    song_id: songId,
    rehearsal_id: rehearsalId,
  });
};
