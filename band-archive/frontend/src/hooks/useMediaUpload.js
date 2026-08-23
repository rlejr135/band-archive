import { useCallback, useEffect, useRef } from 'react';
import { fetchMediaProcessing, normalizeMedia, retryMediaAudio, uploadMediaFile } from '../services/mediaUploadManager';

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const processingStates = new Set(['queued', 'pending', 'processing']);
const processingChanged = (previous, next) => ['transcoding_status', 'audio_url', 'audio_filename', 'processing_error', 'transcoding_error', 'error']
  .some((field) => previous?.[field] !== next?.[field]);

export default function useMediaUpload() {
  const activeRef = useRef(new Map());
  const cancel = useCallback((key) => {
    const active = activeRef.current.get(key);
    if (!active) return;
    active.cancelled = true;
    active.xhrs.forEach((xhr) => xhr.abort());
    if (active.sessionId) fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:5000'}/uploads/multipart/${active.sessionId}/abort`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
  }, []);
  useEffect(() => () => [...activeRef.current.keys()].forEach(cancel), [cancel]);

  const poll = useCallback(async (media, active, onStatus, onMediaUpdate) => {
    let failures = 0;
    while (!active.cancelled && media?.id && processingStates.has(media.transcoding_status)) {
      onStatus(media.transcoding_status === 'processing' ? 'processing' : 'queued', media);
      await wait(Math.min(8000, 1000 * (2 ** failures)));
      try {
        const next = normalizeMedia(await fetchMediaProcessing(media.id));
        const updated = normalizeMedia({ ...media, ...next });
        failures = 0;
        if (processingChanged(media, updated)) onMediaUpdate?.(updated);
        media = updated;
      } catch {
        failures += 1;
        if (failures >= 5) {
          const failed = { ...media, transcoding_status: 'failed', processing_error: '음원 상태 확인에 반복해서 실패했습니다.', error: '음원 상태 확인에 반복해서 실패했습니다.' };
          onStatus('failed', failed); onMediaUpdate?.(failed); return failed;
        }
      }
    }
    if (!active.cancelled) onStatus(media?.transcoding_status === 'failed' ? 'failed' : 'completed', media);
    return media;
  }, []);

  const upload = useCallback(async ({ key, file, songId, rehearsalId, onProgress, onStatus, onMediaUpdate }) => {
    const active = { cancelled: false, xhrs: new Set(), sessionId: null };
    activeRef.current.set(key, active);
    try {
      onStatus('preparing');
      const media = normalizeMedia(await uploadMediaFile({ file, songId, rehearsalId, onProgress, setSessionId: (id) => { active.sessionId = id; }, registerXhr: (xhr) => { active.xhrs.add(xhr); } }));
      if (active.cancelled) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
      onProgress(file.size, file.size); onStatus('queued', media); onMediaUpdate?.(media);
      return await poll(media, active, onStatus, onMediaUpdate);
    } catch (error) {
      if (!active.cancelled) onStatus('failed', { error: error.message });
      throw error;
    } finally { activeRef.current.delete(key); }
  }, [poll]);

  const retryAudio = useCallback(async (mediaId, onStatus, onMediaUpdate) => {
    const media = normalizeMedia(await retryMediaAudio(mediaId));
    const active = { cancelled: false, xhrs: new Set(), sessionId: null };
    const key = `retry-${mediaId}`; activeRef.current.set(key, active);
    try { onStatus('queued', media); return await poll(media, active, onStatus, onMediaUpdate); }
    finally { activeRef.current.delete(key); }
  }, [poll]);

  const watch = useCallback((media, onMediaUpdate) => {
    const key = `watch-${media.id}`;
    const active = { cancelled: false, xhrs: new Set(), sessionId: null };
    activeRef.current.set(key, active);
    poll(normalizeMedia(media), active, () => {}, onMediaUpdate).finally(() => {
      if (activeRef.current.get(key) === active) activeRef.current.delete(key);
    });
    return () => cancel(key);
  }, [cancel, poll]);

  return { upload, cancel, retryAudio, watch };
}
