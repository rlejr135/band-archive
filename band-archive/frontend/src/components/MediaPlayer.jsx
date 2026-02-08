import React, { useState, useRef } from 'react';
import './MediaPlayer.css';

const MediaPlayer = ({ file }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const mediaRef = useRef(null);

  if (!file) return null;

  // Robust media type detection logic
  const getMediaType = (file) => {
    // Priority 1: Use explicitly passed type if valid and not document
    if (file.type && file.type !== 'document') return file.type;
    
    // Priority 2: Extension based detection
    const ext = file.name?.split('.').pop().toLowerCase();
    
    if (['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'].includes(ext)) return 'audio';
    if (['mp4', 'webm', 'mov', 'avi', 'mkv'].includes(ext)) return 'video';
    if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext)) return 'image';
    
    return 'document';
  };

  const mediaType = getMediaType(file);
  const isVideo = mediaType === 'video';
  const isAudio = mediaType === 'audio';
  const isImage = mediaType === 'image';
  const isDocument = mediaType === 'document';
  
  // Use the URL from the file object
  const mediaUrl = file.url;

  const togglePlay = () => {
    if (mediaRef.current) {
      if (isPlaying) {
        mediaRef.current.pause();
      } else {
        mediaRef.current.play();
      }
      setIsPlaying(!isPlaying);
    }
  };

  return (
    <div className="media-player">
      <div className="player-header">
        <span className="player-icon">
          {isVideo && '🎬'}
          {isAudio && '🎵'}
          {isImage && '🖼️'}
          {isDocument && '📄'}
        </span>
        <span className="player-title">{file.name}</span>
      </div>
      
      <div className="player-content">
        {isVideo && mediaUrl && (
          <video 
            ref={mediaRef}
            src={mediaUrl}
            controls
            className="video-player"
            onPlay={() => setIsPlaying(true)}
            onPause={() => setIsPlaying(false)}
          >
            Your browser does not support the video tag.
          </video>
        )}
        
        {isAudio && mediaUrl && (
          <div className="audio-player">
            <audio 
              ref={mediaRef}
              src={mediaUrl}
              controls
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
            >
              Your browser does not support the audio tag.
            </audio>
            
            <div className="audio-controls">
              <button className="play-control" onClick={togglePlay}>
                {isPlaying ? '⏸️ 일시정지' : '▶️ 재생'}
              </button>
            </div>
          </div>
        )}

        {isImage && mediaUrl && (
          <div className="image-preview">
            <img src={mediaUrl} alt={file.name} className="preview-image" />
          </div>
        )}

        {isDocument && mediaUrl && (
          <div className="document-preview">
            <a href={mediaUrl} target="_blank" rel="noreferrer" className="document-link">
              📄 {file.name} 다운로드
            </a>
          </div>
        )}

        {!mediaUrl && (
          <div className="no-preview">
            <p>미리보기를 사용할 수 없습니다</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default MediaPlayer;
