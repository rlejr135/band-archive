const idPart = (value) => value === undefined || value === null || value === '' ? 'none' : String(value);

export const uploadTargetKey = (target = {}) => (
  target.memberId !== undefined && target.memberId !== null && target.memberId !== ''
    ? `personal_log:member:${idPart(target.memberId)}`
    : `media:song:${idPart(target.songId)}:rehearsal:${idPart(target.rehearsalId)}`
);

// Native queue ids identify the picked file, not its destination. Keep the
// destination in the UI task key so one target cannot overwrite another.
export const createUploadTaskId = ({ key, file, target }) => file?.id
  ? `${file.id}:${uploadTargetKey(target)}`
  : key;

/** In-memory browser-file ownership for the app-lifetime upload controller. */
export const createUploadJobRegistry = () => {
  const starts = new Map();
  const sources = new Map();
  const attempts = new Map();
  const cancelled = new Set();
  const nativeOwners = new Map();
  return {
    registerSource: (id, options) => sources.set(id, options),
    sourceFor: (id) => sources.get(id),
    releaseSource: (id) => sources.delete(id),
    cancel: (id) => {
      cancelled.add(id);
      attempts.set(id, (attempts.get(id) || 0) + 1);
    },
    isCancelled: (id) => cancelled.has(id),
    // A callback from an aborted XHR/part retry must not be allowed to update a
    // newer attempt for the same task id.
    isCurrent: (id, attempt) => !cancelled.has(id) && attempts.get(id) === attempt,
    startOnce: (id, start) => {
      if (starts.has(id)) return starts.get(id);
      if (cancelled.has(id)) return Promise.reject(new DOMException('업로드가 취소되었습니다.', 'AbortError'));
      const attempt = (attempts.get(id) || 0) + 1;
      attempts.set(id, attempt);
      const promise = Promise.resolve().then(() => start(attempt)).finally(() => starts.delete(id));
      starts.set(id, promise);
      return promise;
    },
    hasStarted: (id) => starts.has(id),
    runningFor: (id) => starts.get(id),
    claimNativeSource: (nativeId, taskId) => {
      const owner = nativeOwners.get(nativeId);
      if (owner && owner !== taskId) return owner;
      nativeOwners.set(nativeId, taskId);
      return taskId;
    },
    releaseNativeSource: (nativeId, taskId) => {
      if (nativeOwners.get(nativeId) === taskId) nativeOwners.delete(nativeId);
    },
  };
};
