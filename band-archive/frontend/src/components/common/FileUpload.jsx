import React, { useState, useCallback, useId, useEffect, useMemo } from 'react';
import useMediaUpload from '../../hooks/useMediaUpload';
import { consumeNativeUpload, deleteNativeUpload, mergeNativeUploadState, nativeTargetMatches, nativeUploadResult } from '../../services/nativeUploadQueue';
import './FileUpload.css';

const FileUpload = ({ 
  songId,
  rehearsalId,
  memberId,
  onMediaComplete,
  onUpload,
  accept = "audio/*,video/*,image/*,.pdf,.mp3,.wav,.ogg,.m4a,.aac,.flac,.mp4,.webm,.mov,.avi,.mkv,.png,.jpg,.jpeg,.gif,.webp",
  multiple = true 
}) => {
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({});
  const { upload, cancel, transport, nativePending } = useMediaUpload();
  const inputId = useId();
  const nativeTarget = useMemo(() => memberId ? { memberId } : { songId, rehearsalId }, [memberId,rehearsalId,songId]);

  useEffect(() => {
    const matching = nativePending.filter((item) => nativeTargetMatches(item,nativeTarget));
    setUploadProgress((previous) => mergeNativeUploadState(previous, matching, (item) => ({
      name: item.name || '업로드 파일', progress: item.progress || 0, status: item.state, error: item.error,
    })));
  }, [nativePending, nativeTarget]);

  useEffect(() => {
    nativePending.filter((item) => nativeTargetMatches(item,nativeTarget) && item.state === 'completed').forEach((item) => {
      consumeNativeUpload(transport,item.id,nativeTarget,async (terminal) => {
        const media=nativeUploadResult(terminal);
        setUploadProgress((previous) => ({ ...previous, [terminal.id]: { name:terminal.name || '업로드 파일', progress:100, status:terminal.state, error:terminal.error } }));
        if(terminal.state === 'completed') {
          if(!media?.id) throw new Error('업로드 결과를 복구하지 못했습니다.');
          await onMediaComplete?.(media);
        }
      }).catch(() => {});
    });
  }, [nativePending, nativeTarget, onMediaComplete, transport]);

  const deleteTerminal = useCallback(async (fileId) => {
    const deleted = await deleteNativeUpload(transport, fileId, nativeTarget);
    if (deleted) setUploadProgress((previous) => {
      const next = { ...previous }; delete next[fileId]; return next;
    });
  }, [nativeTarget, transport]);

  const handleDragEnter = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleFiles = useCallback(async (files) => {
    for (const file of files) {
      const uniqueId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
      const fileId = file.id || `${file.name}-${file.lastModified}-${uniqueId}`;
      setUploadProgress(prev => ({ ...prev, [fileId]: { name: file.name, progress: 0, status: 'preparing' } }));

      try {
        const updateProgress = (loaded, total) => setUploadProgress(prev => ({ ...prev, [fileId]: { ...prev[fileId], status: 'uploading', progress: total ? Math.round((loaded / total) * 100) : 0 } }));
        const media = onUpload
          ? await onUpload(file, (percent) => updateProgress(percent, 100))
          : await upload({
            key: fileId, file, songId, rehearsalId, memberId,
            onProgress: updateProgress,
            onStatus: (status, mediaState) => setUploadProgress(prev => ({ ...prev, [fileId]: { ...prev[fileId], status, error: mediaState?.error } })),
            onMediaUpdate: onMediaComplete,
          });
        setUploadProgress(prev => ({ ...prev, [fileId]: { ...prev[fileId], status: 'completed', progress: 100 } }));
        onMediaComplete?.(media);
      } catch (error) {
        if (error.name !== 'AbortError') setUploadProgress(prev => ({ ...prev, [fileId]: { ...prev[fileId], status: 'failed', error: error.message } }));
      }
    }
  }, [memberId, onMediaComplete, onUpload, rehearsalId, songId, upload]);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    handleFiles(Array.from(e.dataTransfer.files));
  }, [handleFiles]);

  const handleFileInput = (e) => {
    handleFiles(Array.from(e.target.files));
    e.target.value = '';
  };

  const chooseFiles = useCallback(async () => {
    if (transport.kind === 'native' && !onUpload) {
      try {
        await transport.requestNotificationPermission?.();
        const selected = await transport.pickFiles({ multiple });
        if (selected?.files?.length) handleFiles(selected.files);
      } catch (error) {
        const fileId = `picker-${Date.now()}`;
        setUploadProgress(prev => ({ ...prev, [fileId]: { name: '파일 선택', progress: 0, status: 'failed', error: error.message } }));
      }
      return;
    }
    document.getElementById(inputId).click();
  }, [handleFiles, inputId, multiple, onUpload, transport]);

  const hasActiveUploads = Object.keys(uploadProgress).length > 0;

  return (
    <div className="file-upload-container">
      <div
        className={`drop-zone ${isDragging ? 'dragging' : ''}`}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={chooseFiles}
        role="button"
        tabIndex="0"
        onKeyDown={(e) => e.key === 'Enter' && chooseFiles()}
      >
        <div className="drop-zone-content">
          <div className="upload-icon">📁</div>
          <p className="upload-text">
            파일을 드래그하여 놓거나 클릭하여 선택하세요
          </p>
          <p className="upload-hint">
            영상 최대 1GiB · 100MiB 이상은 분할 업로드
          </p>
          <p className="upload-hint-ios">
            iOS에서 영상 선택 시 변환에 시간이 걸릴 수 있습니다
          </p>
        </div>
        <input
          id={inputId}
          type="file"
          accept={accept}
          multiple={multiple}
          onChange={handleFileInput}
          className="file-input-hidden"
          style={{ display: 'none' }} 
        />
      </div>

      {hasActiveUploads && (
        <div className="upload-progress-list">
          {Object.entries(uploadProgress).map(([fileId, item]) => (
            <div key={fileId} className="progress-item">
              <div className="progress-main">
                <span className="progress-filename">{item.name}</span>
                <span className="progress-status">{{ preparing: '준비 중', queued: '대기 중', uploading: '업로드 중', retry_wait: '재시도 대기 중', completing: '업로드 완료 처리 중', processing: '음원 추출 중', completed: '음원 추출 완료', failed: '실패', cancelled: '취소됨' }[item.status]}{item.status === 'uploading' ? ` (${item.progress}%)` : ''}</span>
                {item.status === 'uploading' && <div className="progress-bar"><div className="progress-fill" style={{ width: `${item.progress}%` }} /></div>}
                {item.error && <span className="progress-error">{item.error}</span>}
              </div>
              {!onUpload && ['preparing', 'queued', 'uploading', 'retry_wait', 'completing', 'processing'].includes(item.status) && <button type="button" className="upload-cancel" onClick={() => cancel(fileId)}>취소</button>}
              {!onUpload && item.status === 'retry_wait' && <button type="button" className="upload-cancel" onClick={() => (transport.retry || transport.resume)?.({ id: fileId })}>재시도</button>}
              {!onUpload && ['failed', 'cancelled'].includes(item.status) && <button type="button" className="upload-cancel" onClick={() => deleteTerminal(fileId)}>삭제</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default FileUpload;
