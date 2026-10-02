import { UploadTransportError } from './uploadTransport.js';

export const bandUploadBasePath = (bandId) => `/api/v2/bands/${Number(bandId)}/uploads`;

const sessionFrom = (response) => ({
  ...response,
  sessionId: response.session_id,
  capabilityToken: response.upload_capability_token,
});

// Every API request intentionally uses the authenticated client, including
// capability endpoints. The capability remains required by the server, while
// bearer authentication lets its 401 refresh/retry policy apply consistently.
export const createBandUploadTransport = ({ bandId, api, putPart }) => {
  if (!Number.isInteger(Number(bandId)) || Number(bandId) <= 0) throw new Error('업로드 밴드 정보가 올바르지 않습니다.');
  if (!api?.requestJson) throw new Error('인증된 업로드 API가 필요합니다.');
  if (typeof putPart !== 'function') throw new Error('브라우저 업로드 전송을 준비하지 못했습니다.');
  const base = bandUploadBasePath(bandId);
  const request = (path, options = {}) => api.requestJson(`${base}${path}`, options);
  const capability = (session, options = {}) => ({
    ...options,
    headers: { ...(options.headers || {}), 'X-Upload-Capability': session.capabilityToken },
  });
  return {
    kind: 'band-web',
    supportsBackground: false,
    initiateDirect: async (payload) => sessionFrom(await request('/direct/initiate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
    completeDirect: (session) => request(`/direct/${encodeURIComponent(session.sessionId)}/complete`, capability(session, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })),
    initiateMultipart: async (payload) => sessionFrom(await request('/multipart/initiate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
    getSession: (session) => request(`/multipart/${encodeURIComponent(session.sessionId)}`, capability(session)),
    requestPart: (session, partNumber) => request(`/multipart/${encodeURIComponent(session.sessionId)}/parts`, capability(session, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ part_number: partNumber }) })),
    acknowledgePart: (session, partNumber, payload) => request(`/multipart/${encodeURIComponent(session.sessionId)}/parts/${Number(partNumber)}/ack`, capability(session, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
    complete: (session) => request(`/multipart/${encodeURIComponent(session.sessionId)}/complete`, capability(session, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })),
    abort: (session) => request(`/multipart/${encodeURIComponent(session.sessionId)}/abort`, capability(session, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })),
    putPart,
    asError: (error) => error instanceof UploadTransportError ? error : error,
  };
};
