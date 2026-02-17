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
