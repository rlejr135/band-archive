export const PRACTICE_PLAYBACK_RATES = [0.75, 1, 1.25, 1.5];
export const MIN_LOOP_SECONDS = 0.25;

const finiteDuration = (duration) => Number.isFinite(duration) && duration > 0;

export const clampPracticeTime = (time, duration) => {
  if (!finiteDuration(duration)) return 0;
  const value = Number.isFinite(time) ? time : 0;
  return Math.min(Math.max(value, 0), duration);
};

export const formatPracticeTime = (time) => {
  const seconds = Math.max(0, Math.floor(Number.isFinite(time) ? time : 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};

export const validatePracticeLoop = ({ start = null, end = null, duration, minSeconds = MIN_LOOP_SECONDS } = {}) => {
  if (!finiteDuration(duration)) return { valid: false, reason: '재생 길이를 확인한 뒤 구간 반복을 설정할 수 있습니다.' };
  if (!Number.isFinite(start) || !Number.isFinite(end)) return { valid: false, reason: 'A 시작과 B 끝을 모두 설정하세요.' };
  const a = clampPracticeTime(start, duration);
  const b = clampPracticeTime(end, duration);
  if (b <= a) return { valid: false, reason: 'B 끝점은 A 시작점보다 뒤여야 합니다.', start: a, end: b };
  if (b - a < minSeconds) return { valid: false, reason: `반복 구간은 ${minSeconds}초 이상이어야 합니다.`, start: a, end: b };
  return { valid: true, start: a, end: b };
};

export const clampPracticeMarkers = ({ start = null, end = null } = {}, duration) => ({
  start: Number.isFinite(start) && finiteDuration(duration) ? clampPracticeTime(start, duration) : null,
  end: Number.isFinite(end) && finiteDuration(duration) ? clampPracticeTime(end, duration) : null,
});

export const getPracticeLoopDecision = ({ time, start, end, duration, enabled }) => {
  const validation = validatePracticeLoop({ start, end, duration });
  if (!enabled || !validation.valid) return { shouldLoop: false, validation };
  return { shouldLoop: Number.isFinite(time) && time >= validation.end, seekTime: validation.start, validation };
};

export const normalizePracticeRate = (rate) => (
  PRACTICE_PLAYBACK_RATES.includes(Number(rate)) ? Number(rate) : 1
);

export const createPracticePlaybackState = () => ({
  currentTime: 0,
  duration: Number.NaN,
  playbackRate: 1,
  markers: { start: null, end: null },
  loopEnabled: false,
  isRadioMode: false,
  isPlaying: false,
});

// A rapid video → radio → video toggle can expose a not-yet-loaded element
// with time 0. Keep the first valid source snapshot until final metadata is
// ready instead of replacing it with that temporary element state.
export const captureModeTransition = (existing, snapshot, sourceKey) => (
  existing?.sourceKey === sourceKey ? existing : { ...snapshot, sourceKey }
);

export const consumeModeTransition = (transition, sourceKey) => (
  transition?.sourceKey === sourceKey ? transition : null
);
