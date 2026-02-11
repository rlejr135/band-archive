import { API_URL } from './api';

// Fetch all members
export const fetchMembers = async () => {
  const response = await fetch(`${API_URL}/members`);
  if (!response.ok) throw new Error('Failed to fetch members');
  return await response.json();
};

// Create new member
export const createMember = async (data) => {
  const response = await fetch(`${API_URL}/members`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to create member');
  return await response.json();
};

// Fetch single member
export const getMember = async (id) => {
  const response = await fetch(`${API_URL}/members/${id}`);
  if (!response.ok) throw new Error('Failed to fetch member');
  return await response.json();
};

// Update member
export const updateMember = async (id, data) => {
  const response = await fetch(`${API_URL}/members/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to update member');
  return await response.json();
};

// Delete member
export const deleteMember = async (id) => {
  const response = await fetch(`${API_URL}/members/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete member');
  return await response.json();
};

// Fetch personal logs for a member
export const fetchMemberLogs = async (memberId) => {
  const response = await fetch(`${API_URL}/members/${memberId}/logs`);
  if (!response.ok) throw new Error('Failed to fetch member logs');
  return await response.json();
};

// Upload personal log
export const uploadPersonalLog = async (memberId, file, title, onProgress) => {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('title', title);

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

    xhr.open('POST', `${API_URL}/members/${memberId}/logs`);
    xhr.send(formData);
  });
};

// Delete personal log
export const deletePersonalLog = async (logId) => {
  const response = await fetch(`${API_URL}/logs/${logId}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete personal log');
  return await response.json();
};
