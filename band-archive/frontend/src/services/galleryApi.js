import { requestJson } from './api';
import { uploadGalleryImageFile } from './mediaUploadManager';

export const fetchGalleryImages = () => requestJson('/gallery', {}, 'Failed to fetch gallery images');

export const uploadGalleryImage = async (file, onProgress) => {
  return uploadGalleryImageFile({ file, onProgress: (loaded, total) => onProgress?.(total ? (loaded / total) * 100 : 0) });
};

export const deleteGalleryImage = (id) => requestJson(`/gallery/${id}`, { method: 'DELETE' }, 'Failed to delete gallery image');

export const setFeaturedImage = (id) => requestJson(`/gallery/${id}/featured`, { method: 'PATCH' }, 'Failed to set featured image');

export const fetchFeaturedImage = () => requestJson('/gallery/featured', {}, 'Failed to fetch featured image');
