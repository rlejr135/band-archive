export const normalizeCommentTimestamp = (value) => {
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
};

export const formatCommentTimestamp = (value) => {
  const seconds = normalizeCommentTimestamp(value);
  if (seconds === null) return null;
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remaining = whole % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
    : `${minutes}:${String(remaining).padStart(2, '0')}`;
};

// undefined means preserve/omit; null means intentionally clear; a finite
// non-negative number deliberately attaches a playback position.
export const withCommentTimestamp = (payload, timestampSeconds) => {
  if (timestampSeconds === undefined) return payload;
  if (timestampSeconds === null) return { ...payload, timestamp_seconds: null };
  const normalized = normalizeCommentTimestamp(timestampSeconds);
  if (normalized === null) throw new Error('댓글 재생 위치가 올바르지 않습니다.');
  return { ...payload, timestamp_seconds: normalized };
};

export const getCommentTimestampSeekPlan = (timestamp, duration) => {
  const seconds = normalizeCommentTimestamp(timestamp);
  if (seconds === null) return { accepted: false };
  if (!Number.isFinite(duration) || duration <= 0) return { accepted: true, pending: true, time: seconds };
  return { accepted: true, pending: false, time: Math.min(seconds, duration) };
};

export const canAttachCommentTimestamp = ({ timestampEnabled, depth = 0 }) => Boolean(timestampEnabled) && depth === 0;

// A controller belongs to exactly one MediaDiscussion card. Keeping this tiny
// dispatch helper pure makes it difficult for a timestamp badge to reach a
// different player on pages that render several media cards.
export const seekCommentTimestamp = (controller, timestamp) => controller?.seekTo?.(timestamp) === true;
