const idPart = (value) => value === undefined || value === null || value === '' ? 'none' : String(value);
const sameId = (left, right) => left !== undefined && left !== null && right !== undefined && right !== null && String(left) === String(right);

export const uploadTargetKey = (target = {}) => (
  `${idPart(target.bandId)}:${target.memberId !== undefined && target.memberId !== null && target.memberId !== ''
    ? `personal_log:member:${idPart(target.memberId)}`
    : `media:song:${idPart(target.songId)}:rehearsal:${idPart(target.rehearsalId)}`}`
);

// Native queue ids identify the picked file, not its destination. Keep the
// destination in the UI task key so one target cannot overwrite another.
export const createUploadTaskId = ({ key, file, target }) => file?.id
  ? `${file.id}:${uploadTargetKey(target)}`
  : key;

export const uploadTaskMatchesTarget = (taskTarget = {}, target = {}) => {
  if (target.bandId !== undefined && target.bandId !== null && !sameId(taskTarget.bandId, target.bandId)) return false;
  if (target.memberId !== undefined && target.memberId !== null) return sameId(taskTarget.memberId, target.memberId);
  if (taskTarget.memberId !== undefined && taskTarget.memberId !== null) return false;
  if (target.songId !== undefined && target.songId !== null && !sameId(taskTarget.songId, target.songId)) return false;
  if (target.rehearsalId !== undefined && target.rehearsalId !== null && !sameId(taskTarget.rehearsalId, target.rehearsalId)) return false;
  return target.songId !== undefined || target.rehearsalId !== undefined;
};

// Route queue rows retain the native plugin's raw id. Resolve it back to the
// qualified global task id before mutating provider state.
export const findUploadTaskByNativeId = (tasks, nativeId, target) => (
  tasks.find((task) => sameId(task.nativeId, nativeId) && uploadTaskMatchesTarget(task.target, target)) || null
);

export const resolveNativeUploadCancellation = (tasks, nativeId, target) => {
  const task = findUploadTaskByNativeId(tasks, nativeId, target);
  return task ? { taskId: task.id, nativeId: task.nativeId } : null;
};

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
