export const normalizeEmail = (value) => (typeof value === 'string' ? value.trim().toLowerCase() : '');

// A return URL is navigation data, not an external URL.  Keep it on this
// origin so auth links cannot become an open redirect.
export const safeReturnPath = (value, origin = globalThis.location?.origin || 'http://localhost') => {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) {
    return null;
  }
  try {
    const url = new URL(value, origin);
    return url.origin === origin ? `${url.pathname}${url.search}${url.hash}` : null;
  } catch {
    return null;
  }
};
