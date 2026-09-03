import { jsonRequest, requestJson } from './api';

// Fetch all members
export const fetchMembers = () => requestJson('/members', {}, 'Failed to fetch members');

// Create new member
export const createMember = (data) => requestJson('/members', jsonRequest('POST', data), 'Failed to create member');

// Fetch single member
export const getMember = (id) => requestJson(`/members/${id}`, {}, 'Failed to fetch member');

// Update member
export const updateMember = (id, data) => requestJson(`/members/${id}`, jsonRequest('PUT', data), 'Failed to update member');

// Delete member
export const deleteMember = (id) => requestJson(`/members/${id}`, { method: 'DELETE' }, 'Failed to delete member');

// Fetch personal logs for a member
export const fetchMemberLogs = (memberId) => requestJson(`/members/${memberId}/logs`, {}, 'Failed to fetch member logs');

// Delete personal log
export const deletePersonalLog = (logId) => requestJson(`/personal-logs/${logId}`, { method: 'DELETE' }, 'Failed to delete personal log');
