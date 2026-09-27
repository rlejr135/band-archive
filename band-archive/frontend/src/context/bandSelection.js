export const selectValidBand = (bandId, bands) => {
  const id = Number(bandId);
  if (!Number.isInteger(id) || id <= 0 || !Array.isArray(bands)) return null;
  return bands.find((band) => Number(band.id) === id) ?? null;
};
