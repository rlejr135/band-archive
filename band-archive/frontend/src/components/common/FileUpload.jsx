import React, { useState, useCallback, useId, useEffect, useMemo, useRef } from 'react';
import useMediaUpload from '../../hooks/useMediaUpload';
import { getUploadStatusLabel, isUploadCancellable } from '../../services/uploadPresentation';
import { mergeNativeUploadState, nativeTargetMatches } from '../../services/nativeUploadQueue';
import { useAuth } from '../../context/AuthContext.jsx';
import { UploadReview } from './UploadReview.jsx';
import './FileUpload.css';

const FileUploadSession = ({
  songId,
  rehearsalId,
  memberId,
  band,
  parentVisibility,
  onMediaComplete,
  onUpload,
  accept = "audio/*,video/*,image/*,.pdf,.mp3,.wav,.ogg,.m4a,.aac,.flac,.mp4,.webm,.mov,.avi,.mkv,.png,.jpg,.jpeg,.gif,.webp",
  multiple = true 
}) => {
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({});
  const [staged, setStaged] = useState([]);
  const live = useRef(true); const starting = useRef(false);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const { uploadBatch, cancel, cancelByNativeId, transport, nativePending, subscribeMediaCompletion } = useMediaUpload();
  const inputId = useId();
  const nativeTarget = useMemo(() => ({ ...(memberId ? { memberId } : { songId, rehearsalId }), bandId: band?.id }), [band?.id,memberId,rehearsalId,songId]);
  const onMediaCompleteRef = useRef(onMediaComplete);

  useEffect(() => { onMediaCompleteRef.current = onMediaComplete; }, [onMediaComplete]);
  useEffect(() => subscribeMediaCompletion(nativeTarget, (event) => {
    if (event.media) onMediaCompleteRef.current?.(event.media);
    // A retained completion means this surface mounted after native ACK. Its
    // owner can refresh once without needing the raw queue item to survive.
    if (event.prior?.length) onMediaCompleteRef.current?.(event.prior.at(-1).media);
  }), [nativeTarget, subscribeMediaCompletion]);

  useEffect(() => {
    const matching = nativePending.filter((item) => nativeTargetMatches(item,nativeTarget));
    setUploadProgress((previous) => mergeNativeUploadState(previous, matching, (item) => ({
      name: item.name || '업로드 파일', progress: item.progress || 0, status: item.state, error: item.error, nativeId: item.id,
    })));
  }, [nativePending, nativeTarget]);

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

  const handleFiles = useCallback((files) => {
    if (starting.current) return;
    setStaged(Array.from(files).slice(0, multiple ? undefined : 1).map((file) => ({ file, id: globalThis.crypto.randomUUID() })));
  }, [multiple]);
  const startFiles = async ({ uploadPolicy }) => {
    if (starting.current || !staged.length || !live.current) return;
    starting.current = true;
    const selected = staged; setStaged([]);
    const patch = (id, value) => { if (live.current) setUploadProgress((previous) => ({ ...previous, [id]: { ...previous[id], ...value } })); };
    const optionsFor = ({ id, file }) => ({ key: id, file, songId, rehearsalId, memberId, band, uploadPolicy,
      onProgress: (loaded, total) => patch(id, { status: 'uploading', progress: total ? Math.round(loaded / total * 100) : 0 }),
      onStatus: (status, mediaState) => patch(id, { status, error: mediaState?.error }),
    });
    selected.forEach(({ id, file }) => patch(id, { name: file.name, status: 'pending', progress: 0, nativeId: file.id || null }));
    try {
      if (onUpload) {
        for (const entry of selected) {
          if (!live.current) break;
          try {
            const media = await onUpload(entry.file, (percent) => patch(entry.id, { status: 'uploading', progress: percent }), { uploadPolicy, isCurrent: () => live.current });
            patch(entry.id, { status: 'completed', progress: 100, error: media?.error });
          } catch (error) { patch(entry.id, { status: error.name === 'AbortError' ? 'cancelled' : 'failed', error: error.message }); }
        }
      } else {
        const results = await uploadBatch({ items: selected.map((entry) => ({ id: entry.id, options: optionsFor(entry) })) });
        results.forEach((result) => patch(result.id, result.error ? { status: result.error.name === 'AbortError' ? 'cancelled' : 'failed', error: result.error.message } : { status: result.media?.transcoding_status === 'failed' ? 'processing_failed' : 'completed', progress: 100, error: result.media?.processing_error || result.media?.error }));
      }
    } catch (error) {
      selected.forEach(({ id }) => patch(id, { status: error.name === 'AbortError' ? 'cancelled' : 'failed', error: '업로드를 시작하지 못했습니다. 현재 권한과 안내를 확인해 주세요.' }));
    } finally { starting.current = false; }
  };

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

      {staged.length > 0 && <UploadReview files={staged} band={band} parentVisibility={parentVisibility} targetLabel={memberId ? `개인 연습 기록 #${memberId}` : songId ? `곡 #${songId}${rehearsalId ? ` · 합주 #${rehearsalId}` : ''}` : '갤러리'} onExclude={(id) => setStaged((previous) => previous.filter((entry) => entry.id !== id))} onCancel={() => setStaged([])} onStart={startFiles} />}

      {hasActiveUploads && (
        <div className="upload-progress-list">
          {Object.entries(uploadProgress).map(([fileId, item]) => (
            <div key={fileId} className="progress-item">
              <div className="progress-main">
                <span className="progress-filename">{item.name}</span>
                <span className="progress-status">{getUploadStatusLabel(item.status, item.progress, 'file')}{item.status === 'uploading' ? ` (${item.progress}%)` : ''}</span>
                {item.status === 'uploading' && <div className="progress-bar"><div className="progress-fill" style={{ width: `${item.progress}%` }} /></div>}
                {item.error && <span className="progress-error">{item.error}</span>}
              </div>
              {!onUpload && isUploadCancellable(item.status) && <button type="button" className="upload-cancel" onClick={() => (item.nativeId ? cancelByNativeId(item.nativeId, nativeTarget) : cancel(fileId))}>취소</button>}
              {!onUpload && item.status === 'retry_wait' && <button type="button" className="upload-cancel" onClick={() => (transport.retry || transport.resume)?.({ id: fileId })}>재시도</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const FileUpload = (props) => {
  const { user, authGeneration = 0 } = useAuth();
  return <FileUploadSession key={`${user?.id || 'anonymous'}:${authGeneration}:${props.band?.id || ''}:${props.songId || ''}:${props.rehearsalId || ''}:${props.memberId || ''}`} {...props} />;
};
export default FileUpload;
