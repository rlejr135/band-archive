import { API_URL } from './api';
import { createTransportCache, createWebUploadTransport, mapUploadState, resolveUploadTransport } from './uploadTransport';

export const MAX_VIDEO_BYTES = 1024 * 1024 * 1024;
export const MULTIPART_THRESHOLD_BYTES = 100 * 1024 * 1024;
const MAX_PART_RETRIES = 3;
const webTransport = createWebUploadTransport({ apiUrl: API_URL });
const runtimeTransport = createTransportCache(() => resolveUploadTransport({ apiUrl: API_URL }));
export const getUploadTransport = () => runtimeTransport.get();
// Call after an explicit Capacitor host/plugin availability transition.
export const invalidateUploadTransport = () => runtimeTransport.invalidate();

// Processing endpoints return { status, error, audio_url, ... }, while regular
// media endpoints use transcoding_status. Keep both endpoint shapes usable by UI.
export const normalizeMedia = (response) => {
  const media = response?.media || response?.personal_log || response;
  if (!media || typeof media !== 'object') return media;
  const status = media.status || media.transcoding_status;
  if (!status) return media;
  const processingError = media.processing_error || media.transcoding_error || media.error;
  return {
    ...media,
    transcoding_status: status,
    processing_error: processingError,
    transcoding_error: media.transcoding_error || processingError,
    error: media.error || processingError,
  };
};

const requestJson = async (url, options = {}) => {
  const response = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  if (!response.ok) {
    let body;
    try { body = await response.json(); } catch { body = null; }
    throw new Error(body?.error || body?.message || `요청에 실패했습니다 (${response.status}).`);
  }
  return response.json();
};

const isVideo = (file) => file.type.startsWith('video/') || /\.(mp4|mov|webm|avi|mkv)$/i.test(file.name);

export const normalizePositiveInteger = (value, fieldName, { optional = false } = {}) => {
  if (optional && (value === undefined || value === null || value === '')) return null;
  const normalized = typeof value === 'number' ? value : Number(String(value).trim());
  if (!Number.isInteger(normalized) || normalized <= 0) throw new Error(`${fieldName}가 올바르지 않습니다. 화면을 새로고침한 뒤 다시 시도하세요.`);
  return normalized;
};

const personalTitle = (file, title) => title || file.name.replace(/\.[^.]+$/, '') || file.name;
export const createUploadTarget = ({ songId, rehearsalId, memberId, title }) => {
  const hasMember = memberId !== undefined && memberId !== null && memberId !== '';
  const hasSongTarget = songId !== undefined && songId !== null && songId !== '';
  const hasRehearsal = rehearsalId !== undefined && rehearsalId !== null && rehearsalId !== '';
  if (hasMember && (hasSongTarget || hasRehearsal)) throw new Error('개인 연습 기록은 곡 또는 합주와 함께 업로드할 수 없습니다.');
  if (hasMember) return { kind: 'personal_log', memberId: normalizePositiveInteger(memberId, '멤버 ID'), title };
  if (!hasSongTarget) throw new Error('곡을 선택한 뒤 다시 시도하세요.');
  return {
    kind: 'media',
    songId: normalizePositiveInteger(songId, '곡 ID'),
    rehearsalId: normalizePositiveInteger(rehearsalId, '합주 ID', { optional: true }),
  };
};

export const nativeTargetPayload = (target) => target.kind === 'personal_log'
  ? { member_id: target.memberId, title: target.title }
  : { song_id: target.songId, rehearsal_id: target.rehearsalId };

const targetPayload = (target, file, filename) => target.kind === 'personal_log'
  ? { filename, original_filename: file.name, file_size: file.size, member_id: target.memberId, title: personalTitle(file, target.title) }
  : { filename, original_filename: file.name, file_size: file.size, song_id: target.songId, rehearsal_id: target.rehearsalId };

const initiatePayload = (target, file) => target.kind === 'personal_log'
  ? { filename: file.name, content_type: file.type || undefined, declared_bytes: file.size, member_id: target.memberId, title: personalTitle(file, target.title) }
  : { filename: file.name, content_type: file.type || undefined, declared_bytes: file.size, song_id: target.songId, rehearsal_id: target.rehearsalId };

const uploadSingle = async ({ file, target, onProgress, registerXhr }) => {
  const contentType = file.type || 'application/octet-stream';
  const presign = await requestJson(`${API_URL}/uploads/presign`, {
    method: 'POST', body: JSON.stringify(target.kind === 'personal_log'
      ? { filename: file.name, content_type: contentType, upload_type: 'personal_log', member_id: target.memberId }
      : { filename: file.name, content_type: contentType, upload_type: 'media' }),
  });
  await webTransport.putPart(presign.upload_url, file, contentType, (loaded) => onProgress(loaded, file.size), registerXhr);
  return requestJson(`${API_URL}${target.kind === 'personal_log' ? '/uploads/complete/personal-log' : '/uploads/complete/media'}`, { method: 'POST', body: JSON.stringify(targetPayload(target, file, presign.filename)) });
};

const acknowledgedParts = (session) => session?.acknowledged_parts || session?.ack_parts || session?.parts || [];

const uploadMultipart = async ({ file, target, onProgress, onUploadState, registerXhr, setSession }) => {
  const initiated = await webTransport.initiateMultipart(initiatePayload(target, file));
  if (!initiated.capabilityToken) throw new Error('업로드 권한 토큰을 받지 못했습니다. 다시 시도하세요.');
  const session = { sessionId: initiated.sessionId, capabilityToken: initiated.capabilityToken };
  setSession?.(session);
  const serverSession = await webTransport.getSession(session);
  const totalParts = Math.ceil(file.size / initiated.part_size);
  if (totalParts > initiated.max_parts) throw new Error('파일이 multipart 업로드 제한을 초과합니다.');
  const completed = new Map();
  acknowledgedParts(serverSession).forEach((part) => {
    const number = part.part_number ?? part.partNumber;
    if (number) completed.set(number, part.bytes ?? Math.min(initiated.part_size, file.size - ((number - 1) * initiated.part_size)));
  });
  const inFlight = new Map();
  const updateAggregate = () => {
    const uploaded = [...completed.values()].reduce((sum, bytes) => sum + bytes, 0);
    const active = [...inFlight.values()].reduce((sum, bytes) => sum + bytes, 0);
    onProgress(Math.min(file.size, uploaded + active), file.size);
  };
  const uploadPart = async (partNumber) => {
    const start = (partNumber - 1) * initiated.part_size;
    const blob = file.slice(start, Math.min(start + initiated.part_size, file.size));
    let lastError;
    let uploadUrl;
    for (let attempt = 0; attempt < MAX_PART_RETRIES; attempt += 1) {
      inFlight.set(partNumber, 0); updateAggregate();
      try {
        if (!uploadUrl) uploadUrl = (await webTransport.requestPart(session, partNumber)).upload_url;
        const etag = await webTransport.putPart(uploadUrl, blob, file.type || 'application/octet-stream', (loaded) => {
          inFlight.set(partNumber, loaded); updateAggregate();
        }, registerXhr);
        if (!etag) throw new Error('R2가 ETag를 노출하지 않았습니다. CORS expose headers를 확인하세요.');
        await webTransport.acknowledgePart(session, partNumber, { etag, bytes: blob.size });
        inFlight.delete(partNumber); completed.set(partNumber, blob.size); updateAggregate();
        return;
      } catch (error) {
        inFlight.delete(partNumber); updateAggregate(); lastError = error;
        if (error.name === 'AbortError') throw error;
        if (attempt < MAX_PART_RETRIES - 1) {
          onUploadState?.(mapUploadState('retry_wait'));
          // A presigned part URL can expire or be rejected; request an idempotent replacement.
          if (error.retryable || error.status === 403) uploadUrl = null;
          await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
          onUploadState?.(mapUploadState('uploading'));
        }
      }
    }
    throw lastError;
  };
  let nextPart = 1;
  const worker = async () => {
    while (nextPart <= totalParts) {
      const part = nextPart; nextPart += 1;
      if (!completed.has(part)) await uploadPart(part);
    }
  };
  await Promise.all(Array.from({ length: Math.min(3, totalParts) }, worker));
  onUploadState?.(mapUploadState('completing'));
  return webTransport.complete(session);
};

export const uploadMediaFile = async (options) => {
  const target = options.target || createUploadTarget(options);
  if (isVideo(options.file) && options.file.size > MAX_VIDEO_BYTES) throw new Error('영상 파일은 1GiB를 초과할 수 없습니다.');
  if (isVideo(options.file) && options.file.size >= MULTIPART_THRESHOLD_BYTES) return uploadMultipart({ ...options, target });
  return uploadSingle({ ...options, target });
};

export const uploadGalleryImageFile = async ({ file, onProgress }) => {
  const contentType = file.type || 'application/octet-stream';
  const presign = await requestJson(`${API_URL}/uploads/presign`, { method: 'POST', body: JSON.stringify({ filename: file.name, content_type: contentType, upload_type: 'gallery' }) });
  await webTransport.putPart(presign.upload_url, file, contentType, (loaded) => onProgress?.(loaded, file.size));
  return requestJson(`${API_URL}/uploads/complete/gallery`, { method: 'POST', body: JSON.stringify({ filename: presign.filename, original_filename: file.name, file_size: file.size }) });
};

export const fetchMediaProcessing = async (mediaId, kind = 'media') => normalizeMedia(await requestJson(`${API_URL}${kind === 'personal_log' ? '/personal-logs' : '/media'}/${mediaId}/processing`));
export const retryMediaAudio = async (mediaId, kind = 'media') => normalizeMedia(await requestJson(`${API_URL}${kind === 'personal_log' ? '/personal-logs' : '/media'}/${mediaId}/retry-audio`, { method: 'POST', body: JSON.stringify({}) }));
export const abortMultipartUpload = (session) => session?.sessionId && session?.capabilityToken ? webTransport.abort(session) : Promise.resolve();
