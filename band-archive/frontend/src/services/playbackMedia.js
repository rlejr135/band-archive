import { safeExternalHttpUrl } from './safeExternalUrl.js';

// Both v2 Media and PersonalLog expose playback sources, but have different
// display names. Processing responses deliberately never supply these URLs.
export const workspaceMediaFile = (content, processing) => {
  const playback = content?.playback || {};
  const video = content?.file_type === 'video';
  const videoUrl = video ? safeExternalHttpUrl(playback.video_url || playback.url) : null;
  const audioUrl = safeExternalHttpUrl(playback.audio_url || (!video ? playback.url : null));
  return {
    id: content?.id,
    type: content?.file_type,
    name: content?.display_name || content?.title || `${video ? '영상' : '음원'} #${content?.id}`,
    url: video ? videoUrl : audioUrl,
    audio_url: audioUrl,
    transcoding_status: video && audioUrl ? 'completed' : (processing?.status || content?.transcoding_status),
    transcoding_error: processing?.error || content?.transcoding_error,
  };
};
