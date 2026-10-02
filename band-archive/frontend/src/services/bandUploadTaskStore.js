const storageKey = (userId) => `band-archive.upload-tasks.v2:${userId || 'anonymous'}`;
const active = new Set(['preparing', 'queued', 'uploading', 'retry_wait', 'completing', 'processing']);
const restartMessage = '브라우저를 새로고침해 이전 업로드 파일을 복원할 수 없습니다. 원래 밴드에서 파일을 다시 선택해 주세요.';
const legacyMessage = '이전 버전 업로드 작업에는 밴드 정보가 없습니다. 현재 밴드로 재전송하지 않았습니다. 원래 파일을 다시 선택해 주세요.';

const safeStorage = () => {
  try { return window.localStorage; } catch { return null; }
};

export const restoreBandUploadTasks = (userId) => {
  const storage = safeStorage();
  if (!storage) return [];
  try {
    const parsed = JSON.parse(storage.getItem(storageKey(userId)) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.map((task) => {
      if (!task?.bandId) return { ...task, file: undefined, retryUnavailable: true, status: 'failed', error: legacyMessage, legacyUnscoped: true };
      if (active.has(task.status)) return { ...task, file: undefined, retryUnavailable: true, status: 'failed', error: restartMessage };
      return { ...task, file: undefined };
    });
  } catch { return []; }
};

export const persistBandUploadTasks = (userId, tasks) => {
  const storage = safeStorage();
  if (!storage) return;
  const serializable = tasks.map((task) => {
    const next = { ...task };
    delete next.file;
    return next;
  }).slice(-40);
  try { storage.setItem(storageKey(userId), JSON.stringify(serializable)); } catch { /* quota/storage disabled */ }
};

export const bandUploadTaskMessages = Object.freeze({ restartMessage, legacyMessage });
