const inProgress = (status) => ['preparing', 'queued', 'uploading', 'retry_wait', 'completing', 'processing'].includes(status);

// Keep task history small without retaining every failed browser File in RAM.
// The rows remain visible, but only the newest failed browser transfer can be
// retried directly; older files must be selected again.
export const retainUploadTaskHistory = (items, { historyLimit = 20, failedFileLimit = 1 } = {}) => {
  const active = items.filter((item) => inProgress(item.status));
  const terminal = items.filter((item) => !inProgress(item.status)).slice(-historyLimit);
  const retained = [...active, ...terminal];
  const retryable = retained.filter((item) => item.status === 'failed' && !item.native && item.file)
    .slice(-failedFileLimit).map((item) => item.id);
  return retained.map((item) => {
    if (item.status !== 'failed' || item.native || retryable.includes(item.id)) return item;
    return { ...item, file: undefined, retryUnavailable: true };
  });
};
