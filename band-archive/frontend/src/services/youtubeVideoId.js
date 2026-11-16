const youtubeHosts = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com']);
const privatePlayerHosts = new Set(['youtube-nocookie.com', 'www.youtube-nocookie.com']);
const videoIdPattern = /^[A-Za-z0-9_-]{11}$/;

// Receives a URL already validated by safeExternalHttpUrl. Return only the
// video ID so user-provided hosts, query parameters and fragments cannot reach the iframe.
export const youtubeVideoId = (safeLink) => {
  if (!safeLink) return null;
  const { hostname, port, pathname, searchParams } = new URL(safeLink);
  if (port) return null;

  let id;
  if (hostname === 'youtu.be') {
    id = /^\/([^/]+)\/?$/.exec(pathname)?.[1];
  } else if (youtubeHosts.has(hostname)) {
    id = pathname === '/watch' ? searchParams.get('v') : /^\/(?:embed|shorts|live)\/([^/]+)\/?$/.exec(pathname)?.[1];
  } else if (privatePlayerHosts.has(hostname)) {
    id = /^\/embed\/([^/]+)\/?$/.exec(pathname)?.[1];
  }
  return id && videoIdPattern.test(id) ? id : null;
};
