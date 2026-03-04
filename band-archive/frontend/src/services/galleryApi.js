import { API_URL } from './api';

export const fetchGalleryImages = async () => {
  const response = await fetch(`${API_URL}/gallery`);
  if (!response.ok) throw new Error('Failed to fetch gallery images');
  return await response.json();
};

export const uploadGalleryImage = (file, onProgress) => {
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
          resolve(JSON.parse(xhr.responseText));
        } catch (e) {
          reject(new Error('Invalid response format'));
        }
      } else {
        reject(new Error(`Upload failed: ${xhr.statusText}`));
      }
    });

    xhr.addEventListener('error', () => reject(new Error('Network error')));
    xhr.open('POST', `${API_URL}/gallery`);
    xhr.send(formData);
  });
};

export const deleteGalleryImage = async (id) => {
  const response = await fetch(`${API_URL}/gallery/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete gallery image');
  return await response.json();
};

export const setFeaturedImage = async (id) => {
  const response = await fetch(`${API_URL}/gallery/${id}/featured`, {
    method: 'PATCH',
  });
  if (!response.ok) throw new Error('Failed to set featured image');
  return await response.json();
};

export const fetchFeaturedImage = async () => {
  const response = await fetch(`${API_URL}/gallery/featured`);
  if (!response.ok) throw new Error('Failed to fetch featured image');
  return await response.json();
};
