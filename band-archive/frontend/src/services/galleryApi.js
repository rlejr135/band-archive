import { API_URL } from './api';
import { uploadGalleryImageFile } from './mediaUploadManager';

export const fetchGalleryImages = async () => {
  const response = await fetch(`${API_URL}/gallery`);
  if (!response.ok) throw new Error('Failed to fetch gallery images');
  return await response.json();
};

export const uploadGalleryImage = async (file, onProgress) => {
  return uploadGalleryImageFile({ file, onProgress: (loaded, total) => onProgress?.(total ? (loaded / total) * 100 : 0) });
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
