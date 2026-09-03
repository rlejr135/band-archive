const YOUTUBE_URL_PATTERN = /^(https?:\/\/)?(www\.)?(youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/shorts\/)[\w-]+/;

export const getYoutubeId = (url) => {
  if (!url) return null;
  const match = url.match(/(?:v=|youtu\.be\/|shorts\/)([\w-]+)/);
  return match ? match[1] : null;
};

export const isValidYoutubeUrl = (url) => !url || YOUTUBE_URL_PATTERN.test(url);
