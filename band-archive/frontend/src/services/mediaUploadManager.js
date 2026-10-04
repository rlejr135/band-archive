import { API_URL } from './api.js';
import { createTransportCache, createWebUploadTransport, mapUploadState, resolveUploadTransport } from './uploadTransport.js';
import { initiateMultipartSession } from './multipartSession.js';
import { createBandGalleryUploadTransport } from './bandUploadTransport.js';

export const MAX_VIDEO_BYTES = 1024 * 1024 * 1024;
export const MULTIPART_THRESHOLD_BYTES = 100 * 1024 * 1024;
const MAX_PART_RETRIES = 3;
const webTransport = createWebUploadTransport({ apiUrl: API_URL });
export const getWebUploadTransport = () => webTransport;
const runtimeTransport = createTransportCache(() => resolveUploadTransport({ apiUrl: API_URL }));
export const getUploadTransport = () => runtimeTransport.get();
// Call after an explicit Capacitor host/plugin availability transition.
export const invalidateUploadTransport = () => runtimeTransport.invalidate();

// v2 processing reads wrap that compact payload as { processing: { status,
// error, audio_url, ... } }, while v1 returns it directly and normal media
// reads use { media } / { personal_log } plus transcoding_status.  Normalize
// all of those boundary shapes into the fields used by the common poller.
export const normalizeMedia = (response) => {
  const media = response?.media || response?.personal_log || response?.processing || response;
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

const isVideo = (file = {}) => String(file.type || file.mimeType || '').startsWith('video/') || /\.(mp4|mov|webm|avi|mkv)$/i.test(file.name || '');

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

const throwIfCancelled = (isCancelled) => {
  if (isCancelled?.()) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
};

const uploadSingle = async ({ file, target, onProgress, registerXhr, isCancelled }) => {
  throwIfCancelled(isCancelled);
  const contentType = file.type || 'application/octet-stream';
  const presign = await requestJson(`${API_URL}/uploads/presign`, {
    method: 'POST', body: JSON.stringify(target.kind === 'personal_log'
      ? { filename: file.name, content_type: contentType, upload_type: 'personal_log', member_id: target.memberId }
      : { filename: file.name, content_type: contentType, upload_type: 'media' }),
  });
  await webTransport.putPart(presign.upload_url, file, contentType, (loaded) => onProgress(loaded, file.size), registerXhr);
  throwIfCancelled(isCancelled);
  return requestJson(`${API_URL}${target.kind === 'personal_log' ? '/uploads/complete/personal-log' : '/uploads/complete/media'}`, { method: 'POST', body: JSON.stringify(targetPayload(target, file, presign.filename)) });
};

const acknowledgedParts = (session) => session?.acknowledged_parts || session?.ack_parts || session?.parts || [];

const uploadMultipart = async ({ file, target, onProgress, onUploadState, registerXhr, setSession, isCancelled, transport = webTransport }) => {
  throwIfCancelled(isCancelled);
  const { initiated, session } = await initiateMultipartSession({
    initiate: () => transport.initiateMultipart(initiatePayload(target, file)),
    setSession: (session) => setSession?.({ ...session, uploadMode: 'multipart' }),
    isCancelled, abort: (current) => transport.abort(current),
  });
  const serverSession = await transport.getSession(session);
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
      throwIfCancelled(isCancelled);
      inFlight.set(partNumber, 0); updateAggregate();
      try {
        if (!uploadUrl) uploadUrl = (await transport.requestPart(session, partNumber)).upload_url;
        throwIfCancelled(isCancelled);
        const etag = await transport.putPart(uploadUrl, blob, file.type || 'application/octet-stream', (loaded) => {
          inFlight.set(partNumber, loaded); updateAggregate();
        }, registerXhr);
        if (!etag) throw new Error('R2가 ETag를 노출하지 않았습니다. CORS expose headers를 확인하세요.');
        await transport.acknowledgePart(session, partNumber, { etag, bytes: blob.size });
        throwIfCancelled(isCancelled);
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
          throwIfCancelled(isCancelled);
          onUploadState?.(mapUploadState('uploading'));
        }
      }
    }
    throw lastError;
  };
  let nextPart = 1;
  const worker = async () => {
    while (nextPart <= totalParts) {
      throwIfCancelled(isCancelled);
      const part = nextPart; nextPart += 1;
      if (!completed.has(part)) await uploadPart(part);
    }
  };
  await Promise.all(Array.from({ length: Math.min(3, totalParts) }, worker));
  throwIfCancelled(isCancelled);
  onUploadState?.(mapUploadState('completing'));
  return transport.complete(session);
};

export const uploadMediaFile = async (options) => {
  const target = options.target || createUploadTarget(options);
  if (isVideo(options.file) && options.file.size > MAX_VIDEO_BYTES) throw new Error('영상 파일은 1GiB를 초과할 수 없습니다.');
  if (isVideo(options.file) && options.file.size >= MULTIPART_THRESHOLD_BYTES) return uploadMultipart({ ...options, target });
  return uploadSingle({ ...options, target });
};

// Direct v2 sessions reserve quota before their presigned PUT. The threshold
// remains identical to the established browser multipart threshold.
export const uploadBandMediaFile = async ({ transport, ...options }) => {
  const target = options.target || createUploadTarget(options);
  const { file, onProgress, registerXhr, setSession, isCancelled } = options;
  if (isVideo(file) && file.size > MAX_VIDEO_BYTES) throw new Error('영상 파일은 1GiB를 초과할 수 없습니다.');
  if (isVideo(file) && file.size >= MULTIPART_THRESHOLD_BYTES) return uploadMultipart({ ...options, target, transport });
  throwIfCancelled(isCancelled);
  const session = await transport.initiateDirect(initiatePayload(target, file));
  setSession?.({ ...session, uploadMode: 'direct' });
  if (isCancelled?.()) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
  await transport.putPart(session.upload_url, file, file.type || 'application/octet-stream', (loaded) => onProgress(loaded, file.size), registerXhr);
  throwIfCancelled(isCancelled);
  return transport.completeDirect(session);
};

export const uploadGalleryImageFile = async ({ file, onProgress }) => {
  const contentType = file.type || 'application/octet-stream';
  const presign = await requestJson(`${API_URL}/uploads/presign`, { method: 'POST', body: JSON.stringify({ filename: file.name, content_type: contentType, upload_type: 'gallery' }) });
  await webTransport.putPart(presign.upload_url, file, contentType, (loaded) => onProgress?.(loaded, file.size));
  return requestJson(`${API_URL}/uploads/complete/gallery`, { method: 'POST', body: JSON.stringify({ filename: presign.filename, original_filename: file.name, file_size: file.size }) });
};

// FileUpload's custom `onUpload` callback consumes a percentage (unlike the
// shared media uploader's byte-based callback). Keep this conversion at the
// gallery boundary so media upload progress contracts remain unchanged.
export const galleryUploadProgressPercent = (loaded, total) => {
  const safeTotal = Number(total);
  const safeLoaded = Number(loaded);
  if (!Number.isFinite(safeTotal) || safeTotal <= 0 || !Number.isFinite(safeLoaded)) return 0;
  return Math.min(100, Math.max(0, (safeLoaded / safeTotal) * 100));
};

// Gallery remains browser-only, but its v2 request chain is still captured to
// one immutable band URL.  The caller supplies isCurrent so a route/account
// switch cannot turn a late completion into a refresh for a new workspace.
export const uploadBandGalleryImageFile = async ({ file, bandId, api, onProgress, isCurrent = () => true }) => {
  if (!file || !Number.isInteger(Number(file.size)) || file.size <= 0) throw new Error('업로드할 이미지 파일이 올바르지 않습니다.');
  const capturedBandId = normalizePositiveInteger(bandId, '밴드 ID');
  const transport = createBandGalleryUploadTransport({
    bandId: capturedBandId,
    api,
    putPart: webTransport.putPart,
  });
  const ensureCurrent = () => {
    if (!isCurrent()) throw new DOMException('밴드가 변경되어 업로드 결과를 적용하지 않았습니다.', 'AbortError');
  };
  const session = await transport.initiate({
    filename: file.name,
    content_type: file.type || undefined,
    declared_bytes: file.size,
  });
  ensureCurrent();
  await transport.putPart(session.upload_url, file, file.type || 'application/octet-stream', (loaded) => {
    onProgress?.(galleryUploadProgressPercent(loaded, file.size));
  });
  ensureCurrent();
  return transport.complete(session);
};

export const fetchMediaProcessing = async (mediaId, kind = 'media') => normalizeMedia(await requestJson(`${API_URL}${kind === 'personal_log' ? '/personal-logs' : '/media'}/${mediaId}/processing`));
export const retryMediaAudio = async (mediaId, kind = 'media') => normalizeMedia(await requestJson(`${API_URL}${kind === 'personal_log' ? '/personal-logs' : '/media'}/${mediaId}/retry-audio`, { method: 'POST', body: JSON.stringify({}) }));
export const abortMultipartUpload = (session) => session?.sessionId && session?.capabilityToken ? webTransport.abort(session) : Promise.resolve();
