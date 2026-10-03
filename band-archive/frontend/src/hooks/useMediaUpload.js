import { Capacitor } from '@capacitor/core';
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { UploadContext } from '../context/UploadContext';
import { useAuth } from '../context/AuthContext';
import { API_URL } from '../services/api';
import { abortMultipartUpload, createUploadTarget, fetchMediaProcessing, getUploadTransport, getWebUploadTransport, MULTIPART_THRESHOLD_BYTES, nativeTargetPayload, normalizeMedia, retryMediaAudio, uploadBandMediaFile, uploadMediaFile } from '../services/mediaUploadManager';
import { isNativeDurableVideo, mapUploadState, shouldUseNativeUpload } from '../services/uploadTransport';
import { createBandUploadTransport } from '../services/bandUploadTransport';
import { createAuthenticatedApiClient } from '../services/authApiClient';
import { claimNativeProcessingRecovery, hydrateNativeUploadQueue, nativeUploadKind, nativeUploadResult, releaseNativeProcessingRecovery, subscribeNativeUploadQueue, syncNativeProcessingStatus, updateNativeUpload } from '../services/nativeUploadQueue';

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const processingStates = new Set(['queued', 'pending', 'processing']);
const processingChanged = (previous, next) => ['transcoding_status', 'audio_url', 'audio_filename', 'processing_error', 'transcoding_error', 'error']
  .some((field) => previous?.[field] !== next?.[field]);

export function useUploadController() {
  const { getAccessToken, refreshSession, user } = useAuth();
  const authenticatedApi = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const activeRef = useRef(new Map());
  const recoveryRef = useRef(new Map());
  const [nativePending, setNativePending] = useState([]);
  const cancel = useCallback((key, nativeId = key) => {
    const active = activeRef.current.get(key);
    if (!active) { const transport=getUploadTransport(); if(transport.kind==='native') transport.cancel({ id: nativeId }).catch(()=>{}); return; }
    active.cancelled = true;
    active.xhrs.forEach((xhr) => xhr.abort());
    active.removeNativeListener?.();
    active.rejectNative?.(new DOMException('업로드가 취소되었습니다.', 'AbortError'));
    if (active.nativeId) active.transport?.cancel({ id: active.nativeId }).catch(() => {});
    active.onStatus?.(mapUploadState('cancelled'));
    if (active.session?.uploadMode !== 'direct') {
      (active.transport?.abort ? active.transport.abort(active.session) : abortMultipartUpload(active.session)).catch(() => {});
    }
  }, []);
  // This controller is mounted once by UploadProvider. User-started transfers must
  // outlive route components; only explicit cancel actions stop them.
  useEffect(() => {
    const transport = getUploadTransport(); let mounted=true;
    hydrateNativeUploadQueue(transport,{ refresh:true }).then((items) => { if(mounted)setNativePending(items); }).catch(() => {});
    return () => { mounted=false; };
  }, []);
  useEffect(() => subscribeNativeUploadQueue(setNativePending), []);
  useEffect(() => {
    const transport = getUploadTransport();
    if (!['android', 'ios'].includes(Capacitor.getPlatform()) || transport.kind !== 'native' || !transport.refreshCredentials) return undefined;
    const accountId = user?.id || null;
    const sync = async () => {
      if (accountId) await transport.refreshCredentials({ accountId });
    };
    sync().catch(() => {});
    return undefined;
  }, [user?.id]);

  const poll = useCallback(async (media, active, onStatus, onMediaUpdate, kind = 'media', readProcessing = fetchMediaProcessing) => {
    let failures = 0;
    while (!active.cancelled && media?.id && processingStates.has(media.transcoding_status)) {
      onStatus(media.transcoding_status === 'processing' ? 'processing' : 'queued', media);
      await wait(Math.min(8000, 1000 * (2 ** failures)));
      if (active.cancelled) return media;
      try {
        const next = normalizeMedia(await readProcessing(media.id, kind));
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

  useEffect(() => {
    const transport=getUploadTransport();
    nativePending.filter((item) => item.state === 'processing' && nativeUploadResult(item)?.id).forEach((item) => {
      if(!claimNativeProcessingRecovery(item.id))return;
      const recovery={ cancelled:false, xhrs:new Set(), session:null };
      recoveryRef.current.set(item.id,recovery);
      const media=normalizeMedia(nativeUploadResult(item));
      const bandId=Number(item.bandId ?? item.target?.band_id);
      const readProcessing=Number.isInteger(bandId)&&bandId>0
        ? (mediaId,kind)=>authenticatedApi.requestJson(`/api/v2/bands/${bandId}/${kind === 'personal_log' ? 'personal-logs' : 'media'}/${mediaId}/processing`)
        : fetchMediaProcessing;
      poll(media,recovery,(state,current) => updateNativeUpload({ id:item.id, state, result:current?JSON.stringify(current):item.result, error:current?.error || item.error }),
        (current) => updateNativeUpload({ id:item.id, state:'processing', result:JSON.stringify(current), error:current?.error || item.error }),nativeUploadKind(item),readProcessing)
        .then(async (current) => {
          if(recovery.cancelled)return;
          const state=current?.transcoding_status === 'failed' ? 'failed' : current?.audio_url ? 'completed' : 'processing';
          updateNativeUpload({ id:item.id, state, result:current?JSON.stringify(current):item.result, error:current?.error || item.error });
          if(state !== 'processing') await syncNativeProcessingStatus(transport,item.id,state,current);
        }).catch(() => {}).finally(() => { releaseNativeProcessingRecovery(item.id); recoveryRef.current.delete(item.id); });
    });
  }, [authenticatedApi,nativePending,poll]);

  const upload = useCallback(async ({ key, file, songId, rehearsalId, memberId, title, band, onProgress, onStatus, onMediaUpdate }) => {
    const active = { cancelled: false, xhrs: new Set(), session: null, onStatus };
    activeRef.current.set(key, active);
    try {
      const target = createUploadTarget({ songId, rehearsalId, memberId, title });
      onStatus(mapUploadState('preparing'));
      const transport = getUploadTransport();
      const bandId = Number(band?.id);
      const isBandUpload = Number.isInteger(bandId) && bandId > 0;
      if (transport.kind === 'native' && shouldUseNativeUpload({ isBandUpload, isNativeVideo: isNativeDurableVideo(file), platform: Capacitor.getPlatform() })) {
        active.transport = transport; active.nativeId = file.id;
        return await new Promise((resolve, reject) => {
          active.rejectNative = reject;
          const finish = (error, media) => {
            active.removeNativeListener?.();
            if (error) reject(error); else resolve(media);
          };
          const begin = async () => {
            try {
            active.removeNativeListener = subscribeNativeUploadQueue(async (snapshot) => {
              const event=snapshot.find((value) => value.id === file.id);
              if(!event)return;
              if (event.id !== file.id || active.cancelled) return;
              const status = mapUploadState(event.state);
              const eventMedia = normalizeMedia(nativeUploadResult(event));
              onProgress((event.progress || 0) * file.size / 100, file.size);
              // Native queue IDs are file UUIDs. Surface a server media record
              // only when one exists so global tasks never mistake the UUID for media.id.
              onStatus(status, eventMedia || { error: event.error });
              if (event.state === 'failed') {
                finish(new Error(event.error || '네이티브 업로드에 실패했습니다.'));
              }
              if (event.state === 'cancelled') finish(new DOMException('업로드가 취소되었습니다.', 'AbortError'));
              if (event.state === 'processing' || event.state === 'completed') {
                let media;
                try { media = normalizeMedia(JSON.parse(event.result || '{}')); } catch { media = null; }
                media = normalizeMedia(media?.media || media?.personal_log || media);
                if (!media?.id) { finish(new Error('서버가 업로드 결과를 반환하지 않았습니다.')); return; }
                if (event.state === 'completed') {
                  onStatus('completed', media);
                  onMediaUpdate?.(media);
                  finish(null, media);
                  return;
                }
                onMediaUpdate?.(media);
                if (active.nativePolling) return;
                active.nativePolling = true;
                const readProcessing = isBandUpload
                  ? (mediaId, kind) => authenticatedApi.requestJson(`/api/v2/bands/${bandId}/${kind === 'personal_log' ? 'personal-logs' : 'media'}/${mediaId}/processing`)
                  : fetchMediaProcessing;
                poll(media, active, onStatus, onMediaUpdate, target.kind, readProcessing)
                  .then(async (result) => {
                    if (active.cancelled) return;
                    const state = result?.transcoding_status === 'failed' ? 'failed' : result?.audio_url ? 'completed' : 'processing';
                    if (state === 'processing') { finish(null, result); return; }
                    await syncNativeProcessingStatus(transport, event.id, state, result);
                  })
                  .catch(finish)
                  .finally(() => { active.nativePolling = false; });
              }
            });
            await hydrateNativeUploadQueue(transport);
            await transport.enqueue({
              fileId: file.id, uri: file.uri, name: file.name, mimeType: file.mimeType || file.type || 'application/octet-stream',
              size: file.size, fingerprint: file.fingerprint, apiUrl: API_URL,
              target: { ...nativeTargetPayload(target), ...(isBandUpload ? { band_id: bandId } : {}) },
              protocol: isBandUpload ? 'v2' : 'v1', mode: isBandUpload && file.size < MULTIPART_THRESHOLD_BYTES ? 'direct' : 'multipart',
              bandId: isBandUpload ? bandId : undefined, bandName: isBandUpload ? (band?.name || `밴드 #${bandId}`) : undefined,
              bandSlug: isBandUpload ? (band?.slug || undefined) : undefined, accountId: isBandUpload ? user?.id : undefined,
            });
            onStatus('queued');
            } catch (error) { finish(error); }
          };
          begin();
        });
      }
      const bandTransport = isBandUpload
        ? createBandUploadTransport({ bandId, api: authenticatedApi, putPart: getWebUploadTransport().putPart })
        : null;
      active.transport = bandTransport || transport;
      const media = normalizeMedia(await (isBandUpload ? uploadBandMediaFile : uploadMediaFile)({ file, target, transport: bandTransport || undefined, onProgress, onUploadState: onStatus, setSession: (session) => { active.session = session; }, isCancelled: () => active.cancelled, registerXhr: (xhr) => { if (active.cancelled) xhr.abort(); else active.xhrs.add(xhr); } }));
      if (active.cancelled) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
      onProgress(file.size, file.size); onStatus('queued', media); onMediaUpdate?.(media);
      const readProcessing = isBandUpload
        ? (mediaId, kind) => authenticatedApi.requestJson(`/api/v2/bands/${bandId}/${kind === 'personal_log' ? 'personal-logs' : 'media'}/${mediaId}/processing`)
        : fetchMediaProcessing;
      const result = await poll(media, active, onStatus, onMediaUpdate, target.kind, readProcessing);
      if (active.cancelled) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
      return result;
    } catch (error) {
      if (!active.cancelled) onStatus('failed', { error: error.message });
      throw error;
    } finally { activeRef.current.delete(key); }
  }, [authenticatedApi, poll, user?.id]);

  const retryAudio = useCallback(async (mediaId, onStatus, onMediaUpdate, kind = 'media', taskKey = null) => {
    const active = { cancelled: false, xhrs: new Set(), session: null, onStatus };
    // The global task id is used when supplied, so its cancel action also
    // stops a server-side audio retry instead of leaving an orphaned poller.
    const key = taskKey || `retry-${kind}-${mediaId}`; activeRef.current.set(key, active);
    try {
      const media = normalizeMedia(await retryMediaAudio(mediaId, kind));
      if (active.cancelled) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
      onStatus('queued', media);
      const result = await poll(media, active, onStatus, onMediaUpdate, kind);
      if (active.cancelled) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
      return result;
    } finally {
      if (activeRef.current.get(key) === active) activeRef.current.delete(key);
    }
  }, [poll]);

  const watch = useCallback((media, onMediaUpdate, kind = 'media') => {
    const key = `watch-${kind}-${media.id}-${globalThis.crypto?.randomUUID?.() || Date.now()}-${Math.random()}`;
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

export default function useMediaUpload() {
  const controller = useContext(UploadContext);
  if (!controller) throw new Error('useMediaUpload must be used inside UploadProvider');
  const { upload: startUpload, retryAudio: startAudioRetry, watch: startWatch, uploadBatch: startBatch } = controller;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const guard = useCallback((callback) => (...args) => {
    if (mounted.current) callback?.(...args);
  }, []);

  const upload = useCallback((options) => startUpload({
    ...options,
    onProgress: guard(options.onProgress),
    onStatus: guard(options.onStatus),
    onMediaUpdate: guard(options.onMediaUpdate),
  }), [guard, startUpload]);
  const retryAudio = useCallback((mediaId, onStatus, onMediaUpdate, kind) => (
    startAudioRetry(mediaId, guard(onStatus), guard(onMediaUpdate), kind)
  ), [guard, startAudioRetry]);
  const watch = useCallback((media, onMediaUpdate, kind) => (
    startWatch(media, guard(onMediaUpdate), kind)
  ), [guard, startWatch]);
  const uploadBatch = useCallback(({ items }) => startBatch({
    items: items.map((item) => ({
      ...item,
      options: {
        ...item.options,
        onProgress: guard(item.options.onProgress),
        onStatus: guard(item.options.onStatus),
        onMediaUpdate: guard(item.options.onMediaUpdate),
      },
    })),
  }), [guard, startBatch]);

  return { ...controller, upload, uploadBatch, retryAudio, watch };
}
