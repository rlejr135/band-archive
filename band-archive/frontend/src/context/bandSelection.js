export const selectValidBand = (bandId, bands) => {
  const id = Number(bandId);
  if (!Number.isInteger(id) || id <= 0 || !Array.isArray(bands)) return null;
  return bands.find((band) => Number(band.id) === id) ?? null;
};

// ``/api/v2/bands`` is expected to return active memberships, but preserve
// the client boundary as well: a public/pending response must never become
// the global account-scoped selection.
export const activeBandsOnly = (bands) => (Array.isArray(bands) ? bands : []).filter((band) => (
  band && Number.isInteger(Number(band.id)) && band.membership?.status === 'active'
));
