import { isActiveBandMembership } from '../services/bandMembership.js';

export const selectValidBand = (bandId, bands) => {
  const id = Number(bandId);
  if (!Number.isInteger(id) || id <= 0 || !Array.isArray(bands)) return null;
  return bands.find((band) => Number(band.id) === id) ?? null;
};

// Only active band memberships can become the account-scoped selection.
export const activeBandsOnly = (bands) => (Array.isArray(bands) ? bands : []).filter((band) => (
  band && Number.isInteger(Number(band.id)) && isActiveBandMembership(band.membership)
));
