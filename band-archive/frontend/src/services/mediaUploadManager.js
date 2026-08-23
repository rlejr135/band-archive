import { API_URL } from './api';

export const MAX_VIDEO_BYTES = 1024 * 1024 * 1024;
export const MULTIPART_THRESHOLD_BYTES = 100 * 1024 * 1024;
const MAX_PART_RETRIES = 3;

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

const putWithProgress = (url, blob, contentType, onProgress, registerXhr) => new Promise((resolve, reject) => {
  const xhr = new XMLHttpRequest();
  registerXhr?.(xhr);
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) onProgress(event.loaded);
  };
  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      const etag = xhr.getResponseHeader('ETag');
      resolve(etag?.replaceAll('"', '') || null);
    } else reject(new Error(`저장소 업로드에 실패했습니다 (${xhr.status}).`));
  };
  xhr.onerror = () => reject(new Error('저장소 네트워크 오류가 발생했습니다.'));
  xhr.onabort = () => reject(new DOMException('업로드가 취소되었습니다.', 'AbortError'));
  xhr.open('PUT', url);
  xhr.setRequestHeader('Content-Type', contentType);
  xhr.send(blob);
});

const isVideo = (file) => file.type.startsWith('video/') || /\.(mp4|mov|webm|avi|mkv)$/i.test(file.name);

const personalTitle = (file, title) => title || file.name.replace(/\.[^.]+$/, '') || file.name;
const isPersonalLog = ({ memberId }) => memberId !== undefined && memberId !== null && memberId !== '';

const assertTarget = (options) => {
  const personal = isPersonalLog(options);
  if (personal && (options.songId !== undefined || options.rehearsalId !== undefined)) throw new Error('개인 연습 기록은 곡 또는 합주와 함께 업로드할 수 없습니다.');
  if (!personal && !options.songId) throw new Error('곡 또는 멤버 업로드 대상이 필요합니다.');
  return personal;
};

const uploadSingle = async ({ file, songId, rehearsalId, memberId, title, onProgress, registerXhr }) => {
  const personal = isPersonalLog({ memberId });
  const contentType = file.type || 'application/octet-stream';
  const presign = await requestJson(`${API_URL}/uploads/presign`, {
    method: 'POST', body: JSON.stringify(personal
      ? { filename: file.name, content_type: contentType, upload_type: 'personal_log', member_id: Number(memberId) }
      : { filename: file.name, content_type: contentType, upload_type: 'media' }),
  });
  await putWithProgress(presign.upload_url, file, contentType, (loaded) => onProgress(loaded, file.size), registerXhr);
  return requestJson(`${API_URL}${personal ? '/uploads/complete/personal-log' : '/uploads/complete/media'}`, {
    method: 'POST',
    body: JSON.stringify(personal
      ? { filename: presign.filename, original_filename: file.name, file_size: file.size, member_id: Number(memberId), title: personalTitle(file, title) }
      : { filename: presign.filename, original_filename: file.name, file_size: file.size, song_id: songId, rehearsal_id: rehearsalId || null }),
  });
};

const uploadMultipart = async ({ file, songId, rehearsalId, memberId, title, onProgress, registerXhr, setSessionId }) => {
  const personal = isPersonalLog({ memberId });
  const initiated = await requestJson(`${API_URL}/uploads/multipart/initiate`, {
    method: 'POST',
    body: JSON.stringify(personal
      ? { filename: file.name, content_type: file.type || undefined, declared_bytes: file.size, member_id: Number(memberId), title: personalTitle(file, title) }
      : { filename: file.name, content_type: file.type || undefined, declared_bytes: file.size, song_id: songId, rehearsal_id: rehearsalId || null }),
  });
  setSessionId?.(initiated.session_id);
  const totalParts = Math.ceil(file.size / initiated.part_size);
  if (totalParts > initiated.max_parts) throw new Error('파일이 multipart 업로드 제한을 초과합니다.');
  const completed = new Map();
  const inFlight = new Map();
  const updateAggregate = () => {
    const uploaded = [...completed.values()].reduce((sum, bytes) => sum + bytes, 0);
    const active = [...inFlight.values()].reduce((sum, bytes) => sum + bytes, 0);
    onProgress(Math.min(file.size, uploaded + active), file.size);
  };
  const uploadPart = async (partNumber) => {
    const start = (partNumber - 1) * initiated.part_size;
    const blob = file.slice(start, Math.min(start + initiated.part_size, file.size));
    // The backend issues one URL per part number and rejects a second issue (409).
    // Reuse that URL for bounded PUT retries.
    const { upload_url: uploadUrl } = await requestJson(`${API_URL}/uploads/multipart/${initiated.session_id}/parts`, { method: 'POST', body: JSON.stringify({ part_number: partNumber }) });
    let lastError;
    for (let attempt = 0; attempt < MAX_PART_RETRIES; attempt += 1) {
      inFlight.set(partNumber, 0); updateAggregate();
      try {
        const etag = await putWithProgress(uploadUrl, blob, file.type || 'application/octet-stream', (loaded) => {
          inFlight.set(partNumber, loaded); updateAggregate();
        }, registerXhr);
        if (!etag) throw new Error('R2가 ETag를 노출하지 않았습니다. CORS expose headers를 확인하세요.');
        inFlight.delete(partNumber); completed.set(partNumber, blob.size); updateAggregate();
        return { part_number: partNumber, etag };
      } catch (error) {
        inFlight.delete(partNumber); updateAggregate(); lastError = error;
        if (error.name === 'AbortError') throw error;
        if (attempt < MAX_PART_RETRIES - 1) await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
      }
    }
    throw lastError;
  };
  const parts = [];
  let nextPart = 1;
  const worker = async () => {
    while (nextPart <= totalParts) {
      const part = nextPart; nextPart += 1;
      parts.push(await uploadPart(part));
    }
  };
  try {
    await Promise.all(Array.from({ length: Math.min(3, totalParts) }, worker));
    return requestJson(`${API_URL}/uploads/multipart/${initiated.session_id}/complete`, { method: 'POST', body: JSON.stringify({ parts: parts.sort((a, b) => a.part_number - b.part_number) }) });
  } catch (error) {
    requestJson(`${API_URL}/uploads/multipart/${initiated.session_id}/abort`, { method: 'POST', body: JSON.stringify({}) }).catch(() => {});
    throw error;
  }
};

export const uploadMediaFile = async (options) => {
  assertTarget(options);
  if (isVideo(options.file) && options.file.size > MAX_VIDEO_BYTES) throw new Error('영상 파일은 1GiB를 초과할 수 없습니다.');
  if (isVideo(options.file) && options.file.size >= MULTIPART_THRESHOLD_BYTES) return uploadMultipart(options);
  return uploadSingle(options);
};

export const fetchMediaProcessing = async (mediaId, kind = 'media') => normalizeMedia(await requestJson(`${API_URL}${kind === 'personal_log' ? '/personal-logs' : '/media'}/${mediaId}/processing`));
export const retryMediaAudio = async (mediaId, kind = 'media') => normalizeMedia(await requestJson(`${API_URL}${kind === 'personal_log' ? '/personal-logs' : '/media'}/${mediaId}/retry-audio`, { method: 'POST', body: JSON.stringify({}) }));
