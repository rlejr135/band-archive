const MAX_EXTERNAL_URL_LENGTH = 2048;
const hasUnsafeCharacters = (value) => /[\s\\\p{C}]/u.test(value);
const hasMalformedPercentEncoding = (value) => /%(?![0-9a-f]{2})/iu.test(value);
const hasPercentEscape = (value) => /%[0-9a-f]{2}/iu.test(value);

const hasUnsafeEncodedCharacters = (value) => {
  if (hasMalformedPercentEncoding(value)) return true;
  let decoded = value;
  // A browser does not repeatedly decode a URL before choosing its protocol,
  // but checking a few nesting levels prevents common double-encoding tricks
  // from smuggling whitespace or controls into a displayed destination.
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (!hasPercentEscape(decoded)) return false;
    try {
      const next = decodeURIComponent(decoded);
      if (hasUnsafeCharacters(next)) return true;
      if (next === decoded) return false;
      decoded = next;
    } catch {
      return true;
    }
  }
  // More nested escapes could still change browser-visible text.  Bound the
  // work but reject that ambiguous input instead of accepting it unchecked.
  return hasPercentEscape(decoded);
};

// Server-provided legacy suggestion links are display-only data. Keep their
// original value untouched, but never turn an ambiguous value into a browser
// navigation target. The returned URL is URL-normalized and safe for href.
export const safeExternalHttpUrl = (value) => {
  if (typeof value !== 'string' || value.length === 0 || value.length > MAX_EXTERNAL_URL_LENGTH) return null;
  if (hasUnsafeCharacters(value) || hasUnsafeEncodedCharacters(value)) return null;
  if (!/^https?:\/\/[^/\\]/i.test(value) || value.includes('\\')) return null;

  try {
    const url = new URL(value);
    if ((url.protocol !== 'http:' && url.protocol !== 'https:') || !url.host || url.username || url.password) return null;
    return url.href.length <= MAX_EXTERNAL_URL_LENGTH ? url.href : null;
  } catch {
    return null;
  }
};
