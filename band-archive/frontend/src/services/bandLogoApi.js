export const bandLogoManagementPath = (slug, { limit = 20, offset = 0 } = {}) => `/api/v2/bands/by-slug/${encodeURIComponent(slug)}/logo-management?limit=${limit}&offset=${offset}`;

export const bandLogoUpdatePath = (bandId) => `/api/v2/bands/${Number(bandId)}/logo`;

export const bandLogoPayload = (galleryImageId) => ({ gallery_image_id: galleryImageId == null ? null : Number(galleryImageId) });
