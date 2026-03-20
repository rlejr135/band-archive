import { API_URL } from './api';

// 1단계: presigned URL 발급
export const getPresignedUrl = async (filename, contentType, uploadType) => {
  const response = await fetch(`${API_URL}/uploads/presign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, content_type: contentType, upload_type: uploadType }),
  });
  if (!response.ok) throw new Error('Failed to get upload URL');
  return response.json(); // { upload_url, key, filename }
};

// 2단계: R2 직접 업로드 (XHR, progress 지원)
export const uploadToStorage = (uploadUrl, file, contentType, onProgress) => {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress((e.loaded / e.total) * 100);
      }
    });
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`Upload failed: ${xhr.status}`));
    });
    xhr.addEventListener('error', () => reject(new Error('Network error')));
    xhr.open('PUT', uploadUrl);
    xhr.setRequestHeader('Content-Type', contentType);
    xhr.send(file); // raw file, not FormData
  });
};

// 3단계: 미디어 메타데이터 등록
export const completeMediaUpload = async (data) => {
  const response = await fetch(`${API_URL}/uploads/complete/media`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to register media');
  return response.json();
};

// 3단계: 갤러리 메타데이터 등록
export const completeGalleryUpload = async (data) => {
  const response = await fetch(`${API_URL}/uploads/complete/gallery`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to register gallery image');
  return response.json();
};
