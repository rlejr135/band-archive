import { safeExternalHttpUrl } from '../../../services/safeExternalUrl.js';

export const mediaPlayerFile = (media) => {
  const playback = media?.playback || {};
  const video = media?.file_type === 'video';
  const audioUrl = video ? safeExternalHttpUrl(playback.audio_url) : undefined;
  return {
    id: media?.id,
    type: media?.file_type,
    name: media?.display_name || `${video ? '영상' : '음원'} #${media?.id}`,
    url: video ? (playback.video_url || playback.url) : (playback.audio_url || playback.url),
    audio_url: audioUrl,
    transcoding_status: video && audioUrl ? 'completed' : media?.transcoding_status,
    transcoding_error: media?.transcoding_error,
  };
};

export const mergeUniqueEntries = (previous, next) => {
  const updates = new Map(next.map((entry) => [String(entry.id), entry]));
  const merged = previous.map((entry) => updates.get(String(entry.id)) || entry);
  const known = new Set(merged.map((entry) => String(entry.id)));
  next.forEach((entry) => { if (!known.has(String(entry.id))) { known.add(String(entry.id)); merged.push(entry); } });
  return merged;
};

export const formatRepresentativeDate = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '등록일 정보 없음' : new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'Asia/Seoul' }).format(date);
};
