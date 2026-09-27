const pageQuery = ({ limit = 20, offset = 0 } = {}) => `?limit=${limit}&offset=${offset}`;

const bandBase = (bandId) => `/api/v2/bands/${Number(bandId)}`;

export const bandWorkspacePaths = {
  bandBySlug: (slug) => `/api/v2/bands/by-slug/${encodeURIComponent(slug)}`,
  songs: (bandId, page) => `${bandBase(bandId)}/songs${pageQuery(page)}`,
  rehearsals: (bandId, page) => `${bandBase(bandId)}/rehearsals${pageQuery(page)}`,
  song: (bandId, songId) => `${bandBase(bandId)}/songs/${Number(songId)}`,
  rehearsal: (bandId, rehearsalId) => `${bandBase(bandId)}/rehearsals/${Number(rehearsalId)}`,
  songMedia: (bandId, songId, page) => `${bandBase(bandId)}/songs/${Number(songId)}/media${pageQuery(page)}`,
  rehearsalMedia: (bandId, rehearsalId, page) => `${bandBase(bandId)}/rehearsals/${Number(rehearsalId)}/media${pageQuery(page)}`,
};

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
