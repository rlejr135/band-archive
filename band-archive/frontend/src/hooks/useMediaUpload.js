import { useCallback, useEffect, useRef, useState } from 'react';
import { API_URL } from '../services/api';
import { abortMultipartUpload, createUploadTarget, fetchMediaProcessing, getUploadTransport, nativeTargetPayload, normalizeMedia, retryMediaAudio, uploadMediaFile } from '../services/mediaUploadManager';
import { mapUploadState } from '../services/uploadTransport';
import { hydrateNativeUploadQueue, subscribeNativeUploadQueue, acknowledgeNativeUpload } from '../services/nativeUploadQueue';

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const processingStates = new Set(['queued', 'pending', 'processing']);
const processingChanged = (previous, next) => ['transcoding_status', 'audio_url', 'audio_filename', 'processing_error', 'transcoding_error', 'error']
  .some((field) => previous?.[field] !== next?.[field]);

export default function useMediaUpload() {
  const activeRef = useRef(new Map());
  const [nativePending, setNativePending] = useState([]);
  const cancel = useCallback((key) => {
    const active = activeRef.current.get(key);
    if (!active) { const transport=getUploadTransport(); if(transport.kind==='native') transport.cancel({id:key}).catch(()=>{}); return; }
    active.cancelled = true;
    active.xhrs.forEach((xhr) => xhr.abort());
    active.removeNativeListener?.();
    active.rejectNative?.(new DOMException('업로드가 취소되었습니다.', 'AbortError'));
    if (active.nativeId) active.transport?.cancel({ id: active.nativeId }).catch(() => {});
    active.onStatus?.(mapUploadState('cancelled'));
    abortMultipartUpload(active.session).catch(() => {});
  }, []);
  useEffect(() => () => [...activeRef.current.keys()].forEach(cancel), [cancel]);
  useEffect(() => { const transport = getUploadTransport(); hydrateNativeUploadQueue(transport).then(setNativePending).catch(() => {}); return subscribeNativeUploadQueue((item) => setNativePending((previous) => [...previous.filter((value) => value.id !== item.id), item])); }, []);

  const poll = useCallback(async (media, active, onStatus, onMediaUpdate, kind = 'media') => {
    let failures = 0;
    while (!active.cancelled && media?.id && processingStates.has(media.transcoding_status)) {
      onStatus(media.transcoding_status === 'processing' ? 'processing' : 'queued', media);
      await wait(Math.min(8000, 1000 * (2 ** failures)));
      try {
        const next = normalizeMedia(await fetchMediaProcessing(media.id, kind));
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

  const upload = useCallback(async ({ key, file, songId, rehearsalId, memberId, title, onProgress, onStatus, onMediaUpdate }) => {
    const active = { cancelled: false, xhrs: new Set(), session: null, onStatus };
    activeRef.current.set(key, active);
    try {
      const target = createUploadTarget({ songId, rehearsalId, memberId, title });
      onStatus(mapUploadState('preparing'));
      const transport = getUploadTransport();
      if (transport.kind === 'native' && file?.uri && file?.id && (file.mimeType || file.type || '').startsWith('video/')) {
        active.transport = transport; active.nativeId = file.id;
        return await new Promise((resolve, reject) => {
          active.rejectNative = reject;
          const finish = (error, media) => {
            active.removeNativeListener?.();
            if (error) reject(error); else resolve(media);
          };
          const begin = async () => {
            try {
            active.removeNativeListener = subscribeNativeUploadQueue(async (event) => {
              if (event.id !== file.id || active.cancelled) return;
              const status = mapUploadState(event.state); onProgress((event.progress || 0) * file.size / 100, file.size); onStatus(status, event);
              if (event.state === 'failed') { await acknowledgeNativeUpload(transport, event.id); finish(new Error(event.error || '네이티브 업로드에 실패했습니다.')); }
              if (event.state === 'cancelled') finish(new DOMException('업로드가 취소되었습니다.', 'AbortError'));
              if (event.state === 'processing' || event.state === 'completed') {
                let media;
                try { media = normalizeMedia(JSON.parse(event.result || '{}')); } catch { media = null; }
                media = normalizeMedia(media?.media || media?.personal_log || media);
                if (!media?.id) { finish(new Error('서버가 업로드 결과를 반환하지 않았습니다.')); return; }
                onMediaUpdate?.(media);
                if (event.state === 'completed') { onStatus('completed', media); await acknowledgeNativeUpload(transport, event.id); finish(null, media); }
                else poll(media, active, onStatus, onMediaUpdate, target.kind).then((result) => finish(null, result)).catch(finish);
              }
            });
            await hydrateNativeUploadQueue(transport);
            await transport.enqueue({
              fileId: file.id, uri: file.uri, name: file.name, mimeType: file.mimeType || file.type || 'application/octet-stream',
              size: file.size, fingerprint: file.fingerprint, apiUrl: API_URL, target: nativeTargetPayload(target),
            });
            onStatus('queued');
            } catch (error) { finish(error); }
          };
          begin();
        });
      }
      const media = normalizeMedia(await uploadMediaFile({ file, target, onProgress, onUploadState: onStatus, setSession: (session) => { active.session = session; }, registerXhr: (xhr) => { active.xhrs.add(xhr); } }));
      if (active.cancelled) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
      onProgress(file.size, file.size); onStatus('queued', media); onMediaUpdate?.(media);
      return await poll(media, active, onStatus, onMediaUpdate, target.kind);
    } catch (error) {
      if (!active.cancelled) onStatus('failed', { error: error.message });
      throw error;
    } finally { activeRef.current.delete(key); }
  }, [poll]);

  const retryAudio = useCallback(async (mediaId, onStatus, onMediaUpdate, kind = 'media') => {
    const media = normalizeMedia(await retryMediaAudio(mediaId, kind));
    const active = { cancelled: false, xhrs: new Set(), session: null, onStatus };
    const key = `retry-${mediaId}`; activeRef.current.set(key, active);
    try { onStatus('queued', media); return await poll(media, active, onStatus, onMediaUpdate, kind); }
    finally { activeRef.current.delete(key); }
  }, [poll]);

  const watch = useCallback((media, onMediaUpdate, kind = 'media') => {
    const key = `watch-${media.id}`;
    const active = { cancelled: false, xhrs: new Set(), session: null };
    activeRef.current.set(key, active);
    poll(normalizeMedia(media), active, () => {}, onMediaUpdate, kind).finally(() => {
      if (activeRef.current.get(key) === active) activeRef.current.delete(key);
    });
    return () => cancel(key);
  }, [cancel, poll]);

  const listPending = useCallback(() => getUploadTransport().listPending(), []);
  const transport = getUploadTransport();
  return { upload, cancel, retryAudio, watch, listPending, transport, nativePending };
}
