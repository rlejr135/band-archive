export const normalizeEmail = (value) => (typeof value === 'string' ? value.trim().toLowerCase() : '');

const hasControlCharacter = (value) => [...value].some((character) => {
  const code = character.charCodeAt(0);
  return code <= 0x1f || code === 0x7f;
});

// A return URL is navigation data, not an external URL.  Keep it on this
// origin so auth links cannot become an open redirect.
export const safeReturnPath = (value, origin = globalThis.location?.origin || 'http://localhost') => {
  const fallback = '/';
  if (typeof value !== 'string' || value !== value.trim() || value.length > 2048 ||
    !value.startsWith('/') || value.includes('\\') || value.includes('%') || value.includes('?') ||
    value.includes('#') || hasControlCharacter(value)) return fallback;
  try {
    const url = new URL(value, origin);
    const pathname = url.pathname;
    if (url.origin !== origin || pathname.includes('//') || !(pathname === '/' || pathname === '/account' || pathname === '/bands' || pathname.startsWith('/bands/'))) {
      return fallback;
    }
    return pathname;
  } catch {
    return fallback;
  }
};

// Invite continuation is intentionally a single fixed local route. Keep this
// separate from safeReturnPath so general auth redirects remain restricted.
export const safeAuthReturnPath = (value, origin = globalThis.location?.origin || 'http://localhost') => {
  if (value === '/invite/continue') return value;
  return safeReturnPath(value, origin);
};
