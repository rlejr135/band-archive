import React, { useState, useCallback } from 'react';
import './FileUpload.css';

const FileUpload = ({ 
  onUpload, 
  accept = ".mp3,.wav,.ogg,.m4a,.aac,.flac,.mp4,.webm,.mov,.avi,.mkv,.png,.jpg,.jpeg,.gif,.webp,.pdf", 
  multiple = true 
}) => {
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({});

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

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    const files = Array.from(e.dataTransfer.files);
    handleFiles(files);
  }, []);

  const handleFileInput = (e) => {
    const files = Array.from(e.target.files);
    handleFiles(files);
    // Reset inputs to allow selecting same file again
    e.target.value = '';
  };

  const handleFiles = async (files) => {
    for (const file of files) {
      if (file.size > 200 * 1024 * 1024) { // 200MB limit
        alert(`'${file.name}' 파일의 크기가 200MB를 초과합니다.`);
        continue;
      }

      const fileId = `${file.name}-${Date.now()}`;
      setUploadProgress(prev => ({ ...prev, [fileId]: 0 }));

      try {
        await onUpload(file, (progress) => {
          setUploadProgress(prev => ({ ...prev, [fileId]: progress }));
        });
        
        // Remove progress after completion
        setTimeout(() => {
          setUploadProgress(prev => {
            const newProgress = { ...prev };
            delete newProgress[fileId];
            return newProgress;
          });
        }, 2000);
      } catch (error) {
        console.error('Upload failed:', error);
        setUploadProgress(prev => {
          const newProgress = { ...prev };
          delete newProgress[fileId];
          return newProgress;
        });
      }
    }
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
        onClick={() => document.getElementById('hidden-file-input').click()}
      >
        <div className="drop-zone-content">
          <div className="upload-icon">📁</div>
          <p className="upload-text">
            파일을 드래그하여 놓거나 클릭하여 선택하세요
          </p>
          <p className="upload-hint">
            음원, 영상, 이미지, 문서 (최대 200MB)
          </p>
        </div>
        <input
          id="hidden-file-input"
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
          {Object.entries(uploadProgress).map(([fileId, progress]) => (
            <div key={fileId} className="progress-item">
              <span className="progress-filename">
                {fileId.split('-')[0]}
              </span>
              <div className="progress-bar">
                <div 
                  className="progress-fill" 
                  style={{ width: `${progress}%` }}
                />
              </div>
              <span className="progress-percent">{Math.round(progress)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default FileUpload;
