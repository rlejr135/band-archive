import { API_URL } from './api.js';

const RESUME_STORAGE_KEY = 'band-invite-resume-context';
const BASE64URL = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const isCanonicalOpaqueId = (value) => typeof value === 'string' && value.length === 43
  && /^[A-Za-z0-9_-]{43}$/.test(value) && (BASE64URL.indexOf(value[42]) & 3) === 0;
let inviteToken = '';
let inviteTokenGeneration = 0;
let callbackResumeContext = '';
let callbackResumeExpiresAt = 0;

const isInvitePath = (pathname) => pathname === '/invite' || pathname === '/invite/';
const setNoReferrer = () => {
  if (typeof document === 'undefined') return;
  let meta = document.querySelector('meta[name="referrer"]');
  if (!meta) {
    meta = document.createElement('meta');
    meta.name = 'referrer';
    document.head.append(meta);
  }
  meta.content = 'no-referrer';
};

export const captureInviteFragment = (location = globalThis.location, history = globalThis.history) => {
  if (!location || !history || !isInvitePath(location.pathname) || !location.hash) return false;
  setNoReferrer();
  inviteToken = '';
  inviteTokenGeneration += 1;
  clearInviteResumeContext();
  const fragment = new URLSearchParams(location.hash.slice(1));
  const tokens = fragment.getAll('token');
  const candidate = tokens.length === 1 ? tokens[0] : '';
  if (isCanonicalOpaqueId(candidate)) inviteToken = candidate;
  history.replaceState(history.state, '', `${location.pathname}${location.search}`);
  return Boolean(inviteToken);
};

// Called synchronously from main.jsx before React or the Supabase client mounts.
// Raw link tokens are captured only in this module's memory, then removed from
// browser history before any route components or third-party widgets render.
export const initializeInviteUrlContext = (location = globalThis.location, history = globalThis.history) => {
  if (!location || !history) return;
  if (isInvitePath(location.pathname)) {
    if (location.hash) captureInviteFragment(location, history);
    else { inviteToken = ''; inviteTokenGeneration += 1; clearInviteResumeContext(); setNoReferrer(); }
    return;
  }
  if (location.pathname === '/verify-email') {
    const query = new URLSearchParams(location.search);
    const contexts = query.getAll('invite_resume');
    const candidate = contexts.length === 1 ? contexts[0] : '';
    if (!query.has('invite_resume')) clearInviteResumeContext();
    if (query.has('invite_resume')) setNoReferrer();
    if (isCanonicalOpaqueId(candidate)) {
      saveInviteResumeContext(candidate, new Date(Date.now() + 60 * 60 * 1000).toISOString());
    }
    if (query.has('invite_resume')) {
      if (contexts.length !== 1 || !isCanonicalOpaqueId(candidate)) clearInviteResumeContext();
      query.delete('invite_resume');
      const remaining = query.toString();
      history.replaceState(history.state, '', `${location.pathname}${remaining ? `?${remaining}` : ''}${location.hash}`);
    }
  }
};

export const inviteContinuationPath = '/invite/continue';

export const isInviteContinuationPath = (value) => value === inviteContinuationPath;

export const hasPendingInviteToken = () => Boolean(inviteToken);

export const getInviteTokenGeneration = () => inviteTokenGeneration;

export const clearPendingInviteToken = () => {
  inviteToken = '';
  inviteTokenGeneration += 1;
};

export const previewInviteLink = async ({ signal } = {}) => {
  if (!inviteToken) throw new Error('invite_token_unavailable');
  const requestGeneration = inviteTokenGeneration;
  const response = await fetch(`${API_URL}/api/v2/invite-links/preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store', signal,
    body: JSON.stringify({ token: inviteToken }),
  });
  if (!response.ok) {
    const error = new Error('invite_preview_failed');
    error.status = response.status;
    throw error;
  }
  const payload = await response.json();
  if (requestGeneration !== inviteTokenGeneration || signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  return payload;
};

export const createInviteResumeContext = async (email, { signal } = {}) => {
  if (!inviteToken) throw new Error('invite_token_unavailable');
  const requestGeneration = inviteTokenGeneration;
  const requestToken = inviteToken;
  const response = await fetch(`${API_URL}/api/v2/invite-links/resume-context`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    cache: 'no-store',
    signal,
    body: JSON.stringify({ token: requestToken, email }),
  });
  if (!response.ok) {
    const error = new Error('invite_resume_context_failed');
    error.status = response.status;
    throw error;
  }
  const payload = await response.json();
  if (requestGeneration !== inviteTokenGeneration || signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  const context = payload?.context;
  if (!isCanonicalOpaqueId(context)) throw new Error('invite_resume_context_invalid');
  const expiresAt = typeof payload.expires_at === 'string' && Number.isFinite(Date.parse(payload.expires_at))
    ? payload.expires_at : new Date(Date.now() + 60 * 60 * 1000).toISOString();
  saveInviteResumeContext(context, expiresAt);
  return context;
};

export const saveInviteResumeContext = (context, expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString()) => {
  if (!isCanonicalOpaqueId(context)) return false;
  const parsedExpiry = Date.parse(expiresAt);
  if (!Number.isFinite(parsedExpiry) || parsedExpiry <= Date.now()) return false;
  callbackResumeContext = context;
  callbackResumeExpiresAt = parsedExpiry;
  try { globalThis.sessionStorage?.setItem(RESUME_STORAGE_KEY, JSON.stringify({ context, expires_at: expiresAt })); } catch { /* module memory remains available */ }
  return true;
};

export const getInviteResumeContext = () => {
  if (callbackResumeContext) {
    if (callbackResumeExpiresAt > Date.now()) return callbackResumeContext;
    clearInviteResumeContext();
  }
  try {
    const stored = globalThis.sessionStorage?.getItem(RESUME_STORAGE_KEY) || '';
    const parsed = JSON.parse(stored);
    if (isCanonicalOpaqueId(parsed?.context) && Number.isFinite(Date.parse(parsed?.expires_at)) && Date.parse(parsed.expires_at) > Date.now()) {
      callbackResumeContext = parsed.context;
      callbackResumeExpiresAt = Date.parse(parsed.expires_at);
      return parsed.context;
    }
    clearInviteResumeContext();
  } catch { /* storage disabled */ }
  return '';
};

export const clearInviteResumeContext = () => {
  callbackResumeContext = '';
  callbackResumeExpiresAt = 0;
  try { globalThis.sessionStorage?.removeItem(RESUME_STORAGE_KEY); } catch { /* storage disabled */ }
};

export const inviteLinkPath = (suffix = '') => `/api/v2/invite-links${suffix}`;

export const inviteManagerPath = (bandId, suffix = '') => `/api/v2/bands/${Number(bandId)}/invite-links${suffix}`;

export const resetInviteUrlMemoryForTests = () => {
  inviteToken = '';
  inviteTokenGeneration += 1;
  callbackResumeContext = '';
  callbackResumeExpiresAt = 0;
};

export const resetInviteUrlContextForTests = () => {
  resetInviteUrlMemoryForTests();
  try { globalThis.sessionStorage?.removeItem(RESUME_STORAGE_KEY); } catch { /* storage disabled */ }
};
