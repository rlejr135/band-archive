const STATUS_LABELS = {
  pending: '준비',
  preparing: '준비 중',
  queued: '대기 중',
  retry_wait: '재시도 대기',
  completing: '완료 처리 중',
  processing: '음원 추출 중',
  completed: '완료',
  failed: '실패',
  cancelled: '취소됨',
};

const FILE_UPLOAD_STATUS_LABELS = {
  ...STATUS_LABELS,
  uploading: '업로드 중',
  retry_wait: '재시도 대기 중',
  completing: '업로드 완료 처리 중',
  completed: '음원 추출 완료',
};

export const getUploadStatusLabel = (status, progress = 0, variant = 'queue') => {
  if (variant === 'file') return FILE_UPLOAD_STATUS_LABELS[status];
  return status === 'uploading' ? `업로드 ${progress}%` : STATUS_LABELS[status];
};

export const isUploadCancellable = (status) => (
  ['preparing', 'queued', 'uploading', 'retry_wait', 'completing', 'processing'].includes(status)
);

export const isUploadTerminal = (status) => ['failed', 'cancelled'].includes(status);
