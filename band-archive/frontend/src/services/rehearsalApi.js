import { jsonRequest, requestJson, voterHeaders } from './api.js';

export const fetchRehearsals = (year, month) => {
  const params = year && month ? `?year=${year}&month=${month}` : '';
  return requestJson(`/rehearsals${params}`, {}, 'Failed to fetch rehearsals');
};

export const getRehearsal = (id) => requestJson(`/rehearsals/${id}`, {}, 'Failed to fetch rehearsal');

export const createRehearsal = (data) => requestJson('/rehearsals', jsonRequest('POST', data), 'Failed to create rehearsal');

export const updateRehearsal = (id, data) => requestJson(`/rehearsals/${id}`, jsonRequest('PUT', data), 'Failed to update rehearsal');

export const deleteRehearsal = (id) => requestJson(`/rehearsals/${id}`, { method: 'DELETE' }, 'Failed to delete rehearsal');

// Fetch media linked to a rehearsal
export const fetchRehearsalMedia = (rehearsalId) => requestJson(
  `/rehearsals/${rehearsalId}/media`,
  { headers: voterHeaders() },
  'Failed to fetch rehearsal media',
);
