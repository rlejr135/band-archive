import React, { createContext, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useUploadController } from '../hooks/useMediaUpload';
import { consumeNativeUpload, nativeTargetMatches, nativeUploadResult, syncNativeProcessingStatus } from '../services/nativeUploadQueue';
import { isUploadInProgress } from '../services/uploadPresentation';
import { createUploadJobRegistry, createUploadTaskId, resolveNativeUploadCancellation } from '../services/uploadJobRegistry';
import { createUploadCompletionStore } from '../services/uploadCompletionStore';
import { getAudioRetryResult } from '../services/uploadRetryResult';
import { retainUploadTaskHistory } from '../services/uploadTaskRetention';
import { persistBandUploadTasks, restoreBandUploadTasks } from '../services/bandUploadTaskStore';
import { useAuth } from './AuthContext';

// eslint-disable-next-line react-refresh/only-export-components
export const UploadContext = createContext(null);
const targetLabel = (target = {}) => {
  if (target.memberId) return '개인 연습 기록';
  if (target.rehearsalId) return `합주 #${target.rehearsalId}`;
  if (target.songId) return `곡 #${target.songId}`;
  return '미디어';
};

export const UploadProvider = ({ children }) => {
  const { user } = useAuth();
  return <UploadTaskProvider key={user?.id || 'anonymous'} userId={user?.id}>{children}</UploadTaskProvider>;
};

const UploadTaskProvider = ({ children, userId }) => {
  const controller = useUploadController();
  const { upload: controllerUpload, cancel: controllerCancel, retryAudio: controllerRetryAudio, nativePending } = controller;
  const [tasks, setTasks] = useState(() => retainUploadTaskHistory(restoreBandUploadTasks(userId)));
  const registry = useRef(createUploadJobRegistry());
  const dismissedNative = useRef(new Set());
  const completions = useRef(createUploadCompletionStore());
  useEffect(() => {
    persistBandUploadTasks(userId, tasks);
  }, [tasks, userId]);

  const publishCompletion = useCallback((target, media, nativeId = null) => (
    completions.current.publish({ target, media, nativeId })
  ), []);

  const updateTask = useCallback((id, patch) => {
    setTasks((previous) => {
      const next = { ...(previous.find((item) => item.id === id) || { id }), ...patch, updatedAt: Date.now() };
      // The browser File is only needed for a transport failure retry. Do not
      // retain completed/cancelled 1 GiB files in the global history.
      if (!isUploadInProgress(next.status) && next.status !== 'failed') {
        delete next.file;
        registry.current.releaseSource(id);
      }
      const bounded = retainUploadTaskHistory([...previous.filter((item) => item.id !== id), next]);
      bounded.filter((item) => item.retryUnavailable).forEach((item) => registry.current.releaseSource(item.id));
      previous.filter((item) => !bounded.some((kept) => kept.id === item.id)).forEach((item) => registry.current.releaseSource(item.id));
      return bounded;
    });
  }, []);

  const upload = useCallback((options) => {
    const { key, file, songId, rehearsalId, memberId, title, band } = options;
    const target = { songId, rehearsalId, memberId, title, bandId: band?.id };
    const bandTask = band?.id ? { bandId: Number(band.id), bandName: band.name || `밴드 #${band.id}`, bandSlug: band.slug || null } : {};
    const id = createUploadTaskId({ key, file, target });
    // Native plugins key durable work by the picked-file id. We intentionally
    // permit duplicate requests for one target to join, while rejecting the
    // same native source for another target until its durable task terminates.
    if (registry.current.hasStarted(id)) return registry.current.runningFor(id);
    if (file?.id && registry.current.claimNativeSource(file.id, id) !== id) {
      const error = '이 파일은 다른 대상에서 이미 업로드 중입니다. 완료 또는 취소 후 다시 시도하세요.';
      updateTask(id, { name: file?.name || '업로드 파일', target, targetLabel: targetLabel(target), ...bandTask, status: 'failed', nativeId: file.id, error });
      return Promise.reject(new Error(error));
    }
    const nativeAlreadyQueued = file?.id && nativePending.some((item) => (
      String(item.id) === String(file.id)
      && nativeTargetMatches(item, { songId, rehearsalId, memberId })
      && isUploadInProgress(item.state)
    ));
    if (nativeAlreadyQueued) return Promise.reject(new Error('이미 대기 중인 네이티브 업로드입니다.'));

    registry.current.registerSource(id, { ...options, key: id });
    if (registry.current.isCancelled(id)) {
      return Promise.reject(new DOMException('업로드가 취소되었습니다.', 'AbortError'));
    }
    updateTask(id, { name: file?.name || '업로드 파일', target, targetLabel: targetLabel(target), ...bandTask, progress: 0, status: 'preparing', file, nativeId: file?.id || null, mediaId: null, error: null, retryUnavailable: false });
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
      const status = media?.transcoding_status === 'failed' ? 'processing_failed' : (isUploadInProgress(media?.transcoding_status) ? 'processing' : 'completed');
      updateTask(id, { status, progress: 100, mediaId: media?.id || null, error: media?.processing_error || media?.error || null });
      if (status === 'completed') publishCompletion(target, media, file?.id || null);
      return media;
    }).catch((error) => {
      if (registry.current.isCurrent(id, attempt)) updateTask(id, { status: error?.name === 'AbortError' ? 'cancelled' : 'failed', error: error.message });
      throw error;
    }));
  }, [controllerUpload, nativePending, publishCompletion, updateTask]);

  const retryTask = useCallback(async (id) => {
    const task = tasks.find((item) => item.id === id);
    if (!task) return { outcome: 'missing', media: null };
    if (task.retryUnavailable) {
      const error = '이전 실패 파일은 메모리 절약을 위해 보관하지 않았습니다. 파일을 다시 선택해 업로드하세요.';
      updateTask(id, { status: 'failed', error, retryUnavailable: true });
      return { outcome: 'failed', media: null, error };
    }
    if (task.native && task.status === 'failed') {
      await (controller.transport.retry || controller.transport.resume)?.({ id: task.nativeId || id });
      return { outcome: 'resumed', media: null };
    }
    if (task.mediaId && task.status === 'processing_failed') {
      // The legacy retry route is deliberately not a fallback for a band-scoped
      // task: it has no v2 band URL to preserve this task's immutable scope.
      if (task.bandId) {
        const error = '이 밴드 업로드의 오디오 재시도는 아직 밴드 범위 API를 지원하지 않습니다. 기존 v1 경로로 전환하지 않았습니다.';
        updateTask(id, { status: 'processing_failed', error, retryUnavailable: true });
        return { outcome: 'failed', media: null, error };
      }
      updateTask(id, { status: 'queued', error: null });
      try {
        const media = await controllerRetryAudio(task.mediaId,
          (status, current) => { if (!registry.current.isCancelled(id)) updateTask(id, { status, error: current?.processing_error || current?.error || null }); },
          (current) => { if (!registry.current.isCancelled(id)) updateTask(id, { mediaId: current?.id || task.mediaId, error: current?.processing_error || current?.error || null }); },
          task.target?.memberId ? 'personal_log' : 'media', id);
        if (task.native && media?.id && !registry.current.isCancelled(id)) {
          const nativeState = media.transcoding_status === 'failed' ? 'failed' : 'completed';
          await syncNativeProcessingStatus(controller.transport, task.nativeId || id, nativeState, media);
        }
        const result = getAudioRetryResult({ media, cancelled: registry.current.isCancelled(id) });
        const { outcome } = result;
        if (!registry.current.isCancelled(id)) {
          updateTask(id, { status: outcome, error: media?.processing_error || media?.error || null });
          if (outcome === 'completed') publishCompletion(task.target, media, task.nativeId);
        }
        return result;
      } catch (error) {
        const result = getAudioRetryResult({ error, cancelled: registry.current.isCancelled(id) });
        if (result.outcome !== 'cancelled') updateTask(id, { status: 'processing_failed', error: result.error });
        return result;
      }
    }
    const source = registry.current.sourceFor(id);
    if (source?.file) {
      try {
        const media = await upload(source);
        return { outcome: media?.transcoding_status === 'failed' ? 'processing_failed' : 'completed', media };
      } catch (error) {
        return { outcome: error?.name === 'AbortError' ? 'cancelled' : 'failed', media: null, error: error?.message };
      }
    }
    return { outcome: 'failed', media: null, error: '재시도할 원본 파일이 없습니다.' };
  }, [controller.transport, controllerRetryAudio, publishCompletion, tasks, updateTask, upload]);

  // The provider, not a route component, owns the serial batch loop so a route
  // change cannot stop later selected files from being submitted.
  const uploadBatch = useCallback(async ({ items }) => {
    items.forEach((item) => {
      const { key, file, songId, rehearsalId, memberId, title, band } = item.options;
      const target = { songId, rehearsalId, memberId, title, bandId: band?.id };
      const id = createUploadTaskId({ key, file, target });
      registry.current.registerSource(id, { ...item.options, key: id });
      if (file?.id) registry.current.claimNativeSource(file.id, id);
      updateTask(id, { name: file?.name || '업로드 파일', target, targetLabel: targetLabel(target), bandId: band?.id ? Number(band.id) : undefined, bandName: band?.name, bandSlug: band?.slug || null, progress: 0, status: 'pending', file, nativeId: file?.id || null, mediaId: null, error: null, retryUnavailable: false });
    });
    const results = [];
    for (const item of items) {
      const target = { songId: item.options.songId, rehearsalId: item.options.rehearsalId, memberId: item.options.memberId, title: item.options.title, bandId: item.options.band?.id };
      const id = createUploadTaskId({ key: item.options.key, file: item.options.file, target });
      if (registry.current.isCancelled(id)) {
        results.push({ ...item, taskId: id, error: new DOMException('업로드가 취소되었습니다.', 'AbortError') });
        continue;
      }
      try { results.push({ ...item, taskId: id, media: await upload(item.options) }); }
      catch (error) { results.push({ ...item, taskId: id, error }); }
    }
    return results;
  }, [updateTask, upload]);

  const cancel = useCallback((id) => {
    // Invalidate the UI attempt before aborting transport work. This prevents
    // a late XHR progress callback or multipart retry from reviving the task.
    registry.current.cancel(id);
    const task = tasks.find((item) => item.id === id);
    controllerCancel(id, task?.nativeId || id);
    updateTask(id, { status: 'cancelled' });
  }, [controllerCancel, tasks, updateTask]);
  const cancelByNativeId = useCallback((nativeId, target) => {
    const resolved = resolveNativeUploadCancellation(tasks, nativeId, target);
    if (!resolved) return false;
    cancel(resolved.taskId);
    return true;
  }, [cancel, tasks]);
  const clearHistory = useCallback(() => setTasks((previous) => {
    previous.filter((item) => !isUploadInProgress(item.status)).forEach((item) => {
      registry.current.releaseSource(item.id);
      if (item.native) {
        dismissedNative.current.add(item.nativeId);
        registry.current.releaseNativeSource(item.nativeId, item.id);
      }
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
      const id = createUploadTaskId({ key: item.id, file: { id: item.id }, target });
      registry.current.claimNativeSource(item.id, id);
      const status = item.state === 'failed' && media?.id && media?.transcoding_status === 'failed'
        ? 'processing_failed'
        : item.state;
      updateTask(id, { name: item.name || '업로드 파일', target, targetLabel: targetLabel(target), progress: item.progress || 0, status, nativeId: item.id, mediaId: media?.id || null, error: item.error || media?.processing_error || null, native: true });
    });
  }, [nativePending, updateTask]);

  // The provider is the sole native terminal acknowledgement owner. Route
  // widgets only observe task snapshots, eliminating consume/ack races.
  useEffect(() => {
    nativePending.filter((item) => item.state === 'completed').forEach((item) => {
      const target = item.target?.member_id
        ? { memberId: item.target.member_id }
        : { songId: item.target?.song_id, rehearsalId: item.target?.rehearsal_id };
      const taskId = createUploadTaskId({ key: item.id, file: { id: item.id }, target });
      const media = nativeUploadResult(item);
      // Record before durable ACK. A target mounting later can see this version
      // even though acknowledge removes the native queue snapshot immediately.
      publishCompletion(target, media, item.id);
      consumeNativeUpload(controller.transport, item.id, target, async () => {}).then((consumed) => {
        if (consumed) registry.current.releaseNativeSource(item.id, taskId);
      }).catch(() => {});
    });
  }, [controller.transport, nativePending, publishCompletion]);

  const subscribeMediaCompletion = useCallback((target, listener) => completions.current.subscribe(target, listener), []);
  const completionVersion = useCallback((target) => completions.current.versionFor(target), []);
  const value = useMemo(() => ({ ...controller, upload, uploadBatch, cancel, cancelByNativeId, retryTask, clearHistory, tasks, subscribeMediaCompletion, completionVersion }), [cancel, cancelByNativeId, clearHistory, completionVersion, controller, retryTask, subscribeMediaCompletion, tasks, upload, uploadBatch]);
  return <UploadContext.Provider value={value}>{children}</UploadContext.Provider>;
};
