// v2 processing reads wrap that compact payload as { processing: { status,
// error, audio_url, ... } }, while v1 returns it directly and normal media
// reads use { media } / { personal_log } plus transcoding_status.  Normalize
// all of those boundary shapes into the fields used by the common poller.
export const normalizeMedia = (response) => {
  const media = response?.media || response?.personal_log || response?.processing || response;
  if (!media || typeof media !== 'object') return media;
  const status = media.status || media.transcoding_status;
  if (!status) return media;
  const processingError = media.processing_error || media.transcoding_error || media.error;
  return {
    ...media,
    transcoding_status: status,
    processing_error: processingError,
    transcoding_error: media.transcoding_error || processingError,
    error: media.error || processingError,
  };
};
