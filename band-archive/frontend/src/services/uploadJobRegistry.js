/** In-memory browser-file ownership for the app-lifetime upload controller. */
export const createUploadJobRegistry = () => {
  const starts = new Map();
  const sources = new Map();
  const attempts = new Map();
  const cancelled = new Set();
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
  };
};
