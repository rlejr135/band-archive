const AUDIO_EXTENSIONS = new Set(['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac']);
const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'avi', 'mkv']);
const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp']);

export const getMediaType = (media = {}) => {
  const declaredType = media.type || media.file_type;
  if (declaredType && declaredType !== 'document') return declaredType;

  const name = media.name || media.filename || '';
  const extension = name.split('.').pop()?.toLowerCase();
  if (AUDIO_EXTENSIONS.has(extension)) return 'audio';
  if (VIDEO_EXTENSIONS.has(extension)) return 'video';
  if (IMAGE_EXTENSIONS.has(extension)) return 'image';
  return 'document';
};

export const getMediaIcon = (media) => ({
  video: '🎬',
  audio: '🎵',
  image: '🖼️',
  document: '📄',
}[getMediaType(media)] || '📁');
