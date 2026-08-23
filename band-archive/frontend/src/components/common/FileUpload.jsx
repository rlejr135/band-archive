import React, { useState, useCallback, useId } from 'react';
import useMediaUpload from '../../hooks/useMediaUpload';
import './FileUpload.css';

const FileUpload = ({ 
  songId,
  rehearsalId,
  memberId,
  onMediaComplete,
  accept = "audio/*,video/*,image/*,.pdf,.mp3,.wav,.ogg,.m4a,.aac,.flac,.mp4,.webm,.mov,.avi,.mkv,.png,.jpg,.jpeg,.gif,.webp",
  multiple = true 
}) => {
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({});
  const { upload, cancel } = useMediaUpload();
  const inputId = useId();

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
      const fileId = `${file.name}-${file.lastModified}-${uniqueId}`;
      setUploadProgress(prev => ({ ...prev, [fileId]: { name: file.name, progress: 0, status: 'preparing' } }));

      try {
        const media = await upload({
          key: fileId, file, songId, rehearsalId, memberId,
          onProgress: (loaded, total) => setUploadProgress(prev => ({ ...prev, [fileId]: { ...prev[fileId], status: 'uploading', progress: total ? Math.round((loaded / total) * 100) : 0 } })),
          onStatus: (status, mediaState) => setUploadProgress(prev => ({ ...prev, [fileId]: { ...prev[fileId], status, error: mediaState?.error } })),
          onMediaUpdate: onMediaComplete,
        });
        onMediaComplete?.(media);
      } catch (error) {
        if (error.name !== 'AbortError') setUploadProgress(prev => ({ ...prev, [fileId]: { ...prev[fileId], status: 'failed', error: error.message } }));
      }
    }
  }, [memberId, onMediaComplete, rehearsalId, songId, upload]);

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

  const hasActiveUploads = Object.keys(uploadProgress).length > 0;

  return (
    <div className="file-upload-container">
      <div
        className={`drop-zone ${isDragging ? 'dragging' : ''}`}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => document.getElementById(inputId).click()}
        role="button"
        tabIndex="0"
        onKeyDown={(e) => e.key === 'Enter' && document.getElementById(inputId).click()}
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
                <span className="progress-status">{{ preparing: '준비 중', uploading: '업로드 중', queued: '업로드 완료 · 음원 대기', processing: '음원 추출 중', completed: '음원 추출 완료', failed: '실패' }[item.status]}{item.status === 'uploading' ? ` (${item.progress}%)` : ''}</span>
                {item.status === 'uploading' && <div className="progress-bar"><div className="progress-fill" style={{ width: `${item.progress}%` }} /></div>}
                {item.error && <span className="progress-error">{item.error}</span>}
              </div>
              {['preparing', 'uploading', 'queued', 'processing'].includes(item.status) && <button type="button" className="upload-cancel" onClick={() => cancel(fileId)}>취소</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default FileUpload;
