export const getAudioRetryResult = ({ media = null, error = null, cancelled = false } = {}) => {
  if (cancelled || error?.name === 'AbortError') return { outcome: 'cancelled', media: null };
  if (error) return { outcome: 'processing_failed', media: null, error: error.message };
  if (media?.transcoding_status === 'failed') return { outcome: 'processing_failed', media };
  if (!media) return { outcome: 'processing_failed', media: null, error: '음원 추출 결과를 받지 못했습니다.' };
  return { outcome: 'completed', media };
};
