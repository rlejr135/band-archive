export class DeletionTaskError extends Error {
  constructor(code = '') { super('Deletion task failed'); this.code = code; }
}

const wait = (milliseconds, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
  const finish = () => { signal?.removeEventListener('abort', abort); resolve(); };
  const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
  const timer = setTimeout(finish, milliseconds);
  signal?.addEventListener('abort', abort, { once: true });
});

export const pollDeletionTask = async ({ api, path, taskId, signal, onStatus = () => {}, pause = wait, retryAfterSeconds, expectedResourceType, expectedResourceId }) => {
  const safeId = String(taskId ?? '');
  if (!safeId || safeId.length > 128) throw new DeletionTaskError('invalid_task');
  let nextDelay = retryAfterSeconds;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const seconds = Number(nextDelay);
    const delay = Number.isFinite(seconds) ? Math.max(0.5, Math.min(seconds, 3)) * 1000 : 1000;
    await pause(delay, signal);
    const task = await api.requestJson(path(safeId), { signal });
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    if ((expectedResourceType && task?.resource_type !== expectedResourceType) || (expectedResourceId != null && Number(task?.resource_id) !== Number(expectedResourceId))) throw new DeletionTaskError('task_mismatch');
    onStatus(task);
    const status = String(task?.status || '').toLowerCase();
    if (status === 'completed' || status === 'succeeded') return task;
    if (status === 'failed' || status === 'cancelled' || status === 'blocked') throw new DeletionTaskError(task?.error_code || status);
    nextDelay = task?.retry_after_seconds;
  }
  throw new DeletionTaskError('timeout');
};

export const deletionErrorMessage = (error) => {
  if (error?.name === 'AbortError') return '';
  if (error?.status === 401) return '로그인 상태를 확인할 수 없습니다. 다시 로그인해 주세요.';
  if (error?.status === 403) return '삭제 권한이 없습니다. 밴드 권한을 새로 확인해 주세요.';
  if (error?.status === 404) return '대상을 찾을 수 없거나 이미 삭제되었습니다. 목록을 새로고침해 주세요.';
  if (error?.status === 409) return '삭제가 보류되었습니다. 연결된 콘텐츠나 진행 중인 파일 처리가 있는지 확인한 뒤 다시 시도해 주세요.';
  if (error instanceof DeletionTaskError && error.code === 'timeout') return '삭제 작업이 아직 완료되지 않았습니다. 잠시 후 목록을 새로고침해 주세요.';
  if (error instanceof DeletionTaskError) return '삭제 작업을 완료하지 못했습니다. 잠시 후 다시 확인해 주세요.';
  return '삭제하지 못했습니다. 권한과 연결 상태를 확인한 뒤 다시 시도해 주세요.';
};
