import React, { createContext, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useUploadController } from '../hooks/useMediaUpload';
import { consumeNativeUpload, nativeTargetMatches, nativeUploadResult, syncNativeProcessingStatus } from '../services/nativeUploadQueue';
import { isUploadInProgress } from '../services/uploadPresentation';
import { createUploadJobRegistry } from '../services/uploadJobRegistry';

// eslint-disable-next-line react-refresh/only-export-components
export const UploadContext = createContext(null);
const HISTORY_LIMIT = 20;

const targetLabel = (target = {}) => {
  if (target.memberId) return '개인 연습 기록';
  if (target.rehearsalId) return `합주 #${target.rehearsalId}`;
  if (target.songId) return `곡 #${target.songId}`;
  return '미디어';
};

const retain = (items) => {
  const active = items.filter((item) => isUploadInProgress(item.status));
  const terminal = items.filter((item) => !isUploadInProgress(item.status)).slice(-HISTORY_LIMIT);
  return [...active, ...terminal];
};

export const UploadProvider = ({ children }) => {
  const controller = useUploadController();
  const { upload: controllerUpload, cancel: controllerCancel, retryAudio: controllerRetryAudio, nativePending } = controller;
  const [tasks, setTasks] = useState([]);
  const registry = useRef(createUploadJobRegistry());
  const dismissedNative = useRef(new Set());

  const updateTask = useCallback((id, patch) => {
    setTasks((previous) => {
      const next = { ...(previous.find((item) => item.id === id) || { id }), ...patch, updatedAt: Date.now() };
      // The browser File is only needed for a transport failure retry. Do not
      // retain completed/cancelled 1 GiB files in the global history.
      if (!isUploadInProgress(next.status) && next.status !== 'failed') {
        delete next.file;
        registry.current.releaseSource(id);
      }
      const retained = retain([...previous.filter((item) => item.id !== id), next]);
      previous.filter((item) => !retained.some((kept) => kept.id === item.id)).forEach((item) => registry.current.releaseSource(item.id));
      return retained;
    });
  }, []);

  const upload = useCallback((options) => {
    const { key, file, songId, rehearsalId, memberId, title } = options;
    const id = file?.id || key;
    const nativeAlreadyQueued = file?.id && nativePending.some((item) => (
      String(item.id) === String(file.id)
      && nativeTargetMatches(item, { songId, rehearsalId, memberId })
      && isUploadInProgress(item.state)
    ));
    if (nativeAlreadyQueued) return Promise.reject(new Error('이미 대기 중인 네이티브 업로드입니다.'));

    const target = { songId, rehearsalId, memberId, title };
    // A duplicate browser event for the same task should join the existing
    // transfer without resetting its visible progress or replacing its File.
    if (registry.current.hasStarted(id)) return registry.current.runningFor(id);
    registry.current.registerSource(id, { ...options, key: id });
    if (registry.current.isCancelled(id)) {
      return Promise.reject(new DOMException('업로드가 취소되었습니다.', 'AbortError'));
    }
    updateTask(id, { name: file?.name || '업로드 파일', target, targetLabel: targetLabel(target), progress: 0, status: 'preparing', file, mediaId: null, error: null });
    return registry.current.startOnce(id, (attempt) => controllerUpload({
      ...options,
      key: id,
      onProgress: (loaded, total) => {
        if (!registry.current.isCurrent(id, attempt)) return;
        updateTask(id, { progress: total ? Math.round((loaded / total) * 100) : 0, status: 'uploading' });
        options.onProgress?.(loaded, total);
      },
      onStatus: (status, media) => {
        if (!registry.current.isCurrent(id, attempt)) return;
        updateTask(id, { status, mediaId: media?.id || null, error: media?.processing_error || media?.error || null });
        options.onStatus?.(status, media);
      },
      onMediaUpdate: (media) => {
        if (!registry.current.isCurrent(id, attempt)) return;
        updateTask(id, media?.transcoding_status === 'failed'
          ? { mediaId: media?.id || null, status: 'processing_failed', error: media?.processing_error || media?.error || null }
          : { mediaId: media?.id || null, error: media?.processing_error || media?.error || null });
        options.onMediaUpdate?.(media);
      },
    }).then((media) => {
      if (!registry.current.isCurrent(id, attempt)) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
      updateTask(id, { status: media?.transcoding_status === 'failed' ? 'processing_failed' : (isUploadInProgress(media?.transcoding_status) ? 'processing' : 'completed'), progress: 100, mediaId: media?.id || null, error: media?.processing_error || media?.error || null });
      return media;
    }).catch((error) => {
      if (registry.current.isCurrent(id, attempt)) updateTask(id, { status: error?.name === 'AbortError' ? 'cancelled' : 'failed', error: error.message });
      throw error;
    }));
  }, [controllerUpload, nativePending, updateTask]);

  const retryTask = useCallback(async (id) => {
    const task = tasks.find((item) => item.id === id);
    if (!task) return;
    if (task.native && task.status === 'failed') {
      await (controller.transport.retry || controller.transport.resume)?.({ id });
      return null;
    }
    if (task.mediaId && task.status === 'processing_failed') {
      updateTask(id, { status: 'queued', error: null });
      try {
        const media = await controllerRetryAudio(task.mediaId,
          (status, current) => { if (!registry.current.isCancelled(id)) updateTask(id, { status, error: current?.processing_error || current?.error || null }); },
          (current) => { if (!registry.current.isCancelled(id)) updateTask(id, { mediaId: current?.id || task.mediaId, error: current?.processing_error || current?.error || null }); },
          task.target?.memberId ? 'personal_log' : 'media', id);
        if (task.native && media?.id && !registry.current.isCancelled(id)) {
          const nativeState = media.transcoding_status === 'failed' ? 'failed' : 'completed';
          await syncNativeProcessingStatus(controller.transport, id, nativeState, media);
        }
        if (!registry.current.isCancelled(id)) updateTask(id, { status: media?.transcoding_status === 'failed' ? 'processing_failed' : 'completed', error: media?.processing_error || media?.error || null });
        return media;
      } catch (error) {
        if (!registry.current.isCancelled(id)) updateTask(id, { status: 'processing_failed', error: error.message });
        return null;
      }
    }
    const source = registry.current.sourceFor(id);
    if (source?.file) {
      try { return await upload(source); } catch { return null; /* Task state already contains the retry error. */ }
    }
    return null;
  }, [controller.transport, controllerRetryAudio, tasks, updateTask, upload]);

  // The provider, not a route component, owns the serial batch loop so a route
  // change cannot stop later selected files from being submitted.
  const uploadBatch = useCallback(async ({ items }) => {
    items.forEach((item) => {
      const { key, file, songId, rehearsalId, memberId, title } = item.options;
      const id = file?.id || key;
      const target = { songId, rehearsalId, memberId, title };
      registry.current.registerSource(id, { ...item.options, key: id });
      updateTask(id, { name: file?.name || '업로드 파일', target, targetLabel: targetLabel(target), progress: 0, status: 'pending', file, mediaId: null, error: null });
    });
    const results = [];
    for (const item of items) {
      const id = item.options.file?.id || item.options.key;
      if (registry.current.isCancelled(id)) {
        results.push({ ...item, error: new DOMException('업로드가 취소되었습니다.', 'AbortError') });
        continue;
      }
      try { results.push({ ...item, media: await upload(item.options) }); }
      catch (error) { results.push({ ...item, error }); }
    }
    return results;
  }, [updateTask, upload]);

  const cancel = useCallback((id) => {
    // Invalidate the UI attempt before aborting transport work. This prevents
    // a late XHR progress callback or multipart retry from reviving the task.
    registry.current.cancel(id);
    controllerCancel(id);
    updateTask(id, { status: 'cancelled' });
  }, [controllerCancel, updateTask]);
  const clearHistory = useCallback(() => setTasks((previous) => {
    previous.filter((item) => !isUploadInProgress(item.status)).forEach((item) => {
      registry.current.releaseSource(item.id);
      if (item.native) dismissedNative.current.add(item.id);
    });
    return previous.filter((item) => isUploadInProgress(item.status));
  }), []);

  useEffect(() => {
    nativePending.forEach((item) => {
      if (dismissedNative.current.has(item.id)) {
        if (isUploadInProgress(item.state)) dismissedNative.current.delete(item.id);
        else return;
      }
      const target = item.target?.member_id ? { memberId: item.target.member_id } : { songId: item.target?.song_id, rehearsalId: item.target?.rehearsal_id };
      const media = nativeUploadResult(item);
      const status = item.state === 'failed' && media?.id && media?.transcoding_status === 'failed'
        ? 'processing_failed'
        : item.state;
      updateTask(item.id, { name: item.name || '업로드 파일', target, targetLabel: targetLabel(target), progress: item.progress || 0, status, mediaId: media?.id || null, error: item.error || media?.processing_error || null, native: true });
    });
  }, [nativePending, updateTask]);

  // The provider is the sole native terminal acknowledgement owner. Route
  // widgets only observe task snapshots, eliminating consume/ack races.
  useEffect(() => {
    nativePending.filter((item) => item.state === 'completed').forEach((item) => {
      const target = item.target?.member_id
        ? { memberId: item.target.member_id }
        : { songId: item.target?.song_id, rehearsalId: item.target?.rehearsal_id };
      consumeNativeUpload(controller.transport, item.id, target, async () => {}).catch(() => {});
    });
  }, [controller.transport, nativePending]);

  const value = useMemo(() => ({ ...controller, upload, uploadBatch, cancel, retryTask, clearHistory, tasks }), [cancel, clearHistory, controller, retryTask, tasks, upload, uploadBatch]);
  return <UploadContext.Provider value={value}>{children}</UploadContext.Provider>;
};
