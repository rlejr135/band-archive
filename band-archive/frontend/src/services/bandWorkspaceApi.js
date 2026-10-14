const pageQuery = ({ limit = 20, offset = 0 } = {}) => `?limit=${limit}&offset=${offset}`;

const bandBase = (bandId) => `/api/v2/bands/${Number(bandId)}`;

export const bandWorkspacePaths = {
  bandBySlug: (slug) => `/api/v2/bands/by-slug/${encodeURIComponent(slug)}`,
  dashboard: (bandId, { month, limit = 25, offset = 0 } = {}) => `${bandBase(bandId)}/dashboard?month=${encodeURIComponent(month)}&limit=${limit}&offset=${offset}`,
  songs: (bandId, page) => `${bandBase(bandId)}/songs${pageQuery(page)}`,
  rehearsals: (bandId, page) => `${bandBase(bandId)}/rehearsals${pageQuery(page)}`,
  searchPlaces: (bandId, query) => `${bandBase(bandId)}/search-places?query=${encodeURIComponent(String(query || '').slice(0, 100))}`,
  song: (bandId, songId) => `${bandBase(bandId)}/songs/${Number(songId)}`,
  rehearsal: (bandId, rehearsalId) => `${bandBase(bandId)}/rehearsals/${Number(rehearsalId)}`,
  songMedia: (bandId, songId, page) => `${bandBase(bandId)}/songs/${Number(songId)}/media${pageQuery(page)}`,
  rehearsalMedia: (bandId, rehearsalId, page) => `${bandBase(bandId)}/rehearsals/${Number(rehearsalId)}/media${pageQuery(page)}`,
  mediaVote: (bandId, mediaId) => `${bandBase(bandId)}/media/${Number(mediaId)}/vote`,
  mediaComments: (bandId, mediaId, page) => `${bandBase(bandId)}/media/${Number(mediaId)}/comments${pageQuery(page)}`,
  mediaCommentsWrite: (bandId, mediaId) => `${bandBase(bandId)}/media/${Number(mediaId)}/comments`,
  comment: (bandId, commentId) => `${bandBase(bandId)}/comments/${Number(commentId)}`,
  members: (bandId, page) => `${bandBase(bandId)}/members${pageQuery(page)}`,
  member: (bandId, memberId) => `${bandBase(bandId)}/members/${Number(memberId)}`,
  memberLogs: (bandId, memberId, page) => `${bandBase(bandId)}/members/${Number(memberId)}/logs${pageQuery(page)}`,
  personalLogProcessing: (bandId, logId) => `${bandBase(bandId)}/personal-logs/${Number(logId)}/processing`,
  gallery: (bandId, page) => `${bandBase(bandId)}/gallery${pageQuery(page)}`,
  featuredGallery: (bandId) => `${bandBase(bandId)}/gallery/featured`,
  galleryFeatured: (bandId, galleryId) => `${bandBase(bandId)}/gallery/${Number(galleryId)}/featured`,
  announcement: (bandId) => `${bandBase(bandId)}/announcement`,
  suggestions: (bandId, page) => `${bandBase(bandId)}/suggestions${pageQuery(page)}`,
  suggestion: (bandId, suggestionId) => `${bandBase(bandId)}/suggestions/${Number(suggestionId)}`,
  suggestionVote: (bandId, suggestionId) => `${bandBase(bandId)}/suggestions/${Number(suggestionId)}/vote`,
  suggestionProceed: (bandId, suggestionId) => `${bandBase(bandId)}/suggestions/${Number(suggestionId)}/proceed`,
};

// v2 derives author, ownership and band scope from the authenticated
// membership.  These payloads intentionally exclude every legacy field.
export const commentPayload = ({ content, parentId = null }) => ({
  content,
  ...(parentId == null ? {} : { parent_id: Number(parentId) }),
});

export const votePayload = (value) => ({ value });

export const suggestionPayload = (value) => ({
  title: value.title,
  artist: value.artist,
  link: value.link,
  memo: value.memo,
  visibility: value.visibility,
});

// Ownership and tenant columns are intentionally not accepted here. The v2
// server derives them from the authenticated membership and URL-resolved band.
export const songPayload = (value) => ({
  title: value.title,
  artist: value.artist,
  status: value.status,
  chords: value.chords,
  link: value.link,
  memo: value.memo,
  genre: value.genre,
  difficulty: value.difficulty === '' ? undefined : Number(value.difficulty),
  visibility: value.visibility,
});

export const rehearsalPayload = (value) => ({
  title: value.title,
  date: value.date,
  start_date: value.start_date || null,
  end_date: value.end_date || null,
  time: value.time,
  location: value.location,
  memo: value.memo,
  color: value.color,
  visibility: value.visibility,
  song_ids: (value.song_ids || []).map(Number).filter(Number.isInteger),
});
