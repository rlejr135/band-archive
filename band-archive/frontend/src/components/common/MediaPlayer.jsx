import React, { useState, useRef, useEffect } from 'react';
import './MediaPlayer.css';

const MediaPlayer = ({ file }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentQuality, setCurrentQuality] = useState('original');
  const [isRadioMode, setIsRadioMode] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const mediaRef = useRef(null);
  const settingsRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (settingsRef.current && !settingsRef.current.contains(event.target)) {
        setShowSettings(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (!file) return null;

  const getMediaType = (file) => {
    if (file.type && file.type !== 'document') return file.type;
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

  // Quality and Source Logic
  const hasQualities = file.qualities && Object.keys(file.qualities).length > 0;
  
  const getMediaUrl = () => {
    if (!hasQualities) return file.url;
    
    if (isRadioMode && file.qualities.audio) {
      return file.qualities.audio;
    }
    
    return file.qualities[currentQuality] || file.qualities.original || file.url;
  };

  const mediaUrl = getMediaUrl();

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

  const handleSourceChange = (changeFn) => {
    if (mediaRef.current) {
      const currentTime = mediaRef.current.currentTime;
      const wasPlaying = !mediaRef.current.paused;
      
      changeFn();
      setShowSettings(false);

      const restore = () => {
        if (mediaRef.current) {
          mediaRef.current.currentTime = currentTime;
          if (wasPlaying) {
            mediaRef.current.play().catch(e => console.log('Auto-play blocked or failed', e));
          }
        }
        mediaRef.current?.removeEventListener('loadedmetadata', restore);
      };
      
      // Set timeout as fallback or use loadedmetadata
      mediaRef.current.addEventListener('loadedmetadata', restore);
      // Fallback for some browsers or scenarios where loadedmetadata might not fire as expected
      setTimeout(() => {
        if (mediaRef.current && Math.abs(mediaRef.current.currentTime - currentTime) > 0.5 && currentTime > 0) {
           // already handled or need manual check
        }
      }, 1000);
    } else {
      changeFn();
      setShowSettings(false);
    }
  };

  const availableQualities = hasQualities 
    ? Object.keys(file.qualities).filter(q => q !== 'audio') 
    : [];

  return (
    <div className="media-player">
      <div className="player-header">
        <div className="player-info">
          <span className="player-icon">
            {isRadioMode ? '📻' : (isVideo ? '🎬' : (isAudio ? '🎵' : (isImage ? '🖼️' : '📄')))}
          </span>
          <span className="player-title">{file.name}</span>
        </div>
        
        {(isVideo || isAudio) && hasQualities && (
          <div className="settings-wrapper" ref={settingsRef}>
            <button 
              className={`settings-toggle ${showSettings ? 'active' : ''}`}
              onClick={() => setShowSettings(!showSettings)}
              title="설정"
            >
              ⚙️
            </button>
            {showSettings && (
              <div className="settings-menu">
                {isVideo && (
                  <div className="settings-section">
                    <div className="settings-label">재생 모드</div>
                    <button 
                      className={`settings-item ${!isRadioMode ? 'selected' : ''}`}
                      onClick={() => handleSourceChange(() => setIsRadioMode(false))}
                    >
                      비디오 모드
                    </button>
                    <button 
                      className={`settings-item ${isRadioMode ? 'selected' : ''}`}
                      onClick={() => handleSourceChange(() => setIsRadioMode(true))}
                    >
                      라디오 모드
                    </button>
                  </div>
                )}
                
                {!isRadioMode && availableQualities.length > 1 && (
                  <div className="settings-section">
                    <div className="settings-label">화질 선택</div>
                    {availableQualities.map(q => (
                      <button 
                        key={q}
                        className={`settings-item ${currentQuality === q ? 'selected' : ''}`}
                        onClick={() => handleSourceChange(() => setCurrentQuality(q))}
                      >
                        {q === 'original' ? '원본 화질' : q}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      
      <div className="player-content">
        {!isRadioMode && isVideo && mediaUrl && (
          <video 
            key={`video-${currentQuality}`}
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
        
        {(isRadioMode || isAudio) && mediaUrl && (
          <div className="audio-player-container">
            <div className="audio-visualizer">
              <div className="album-art">
                {isRadioMode ? '📻' : '🎵'}
              </div>
              <div className="audio-info">
                <div className="audio-mode-badge">{isRadioMode ? 'RADIO MODE' : 'AUDIO'}</div>
                <div className="audio-filename">{file.name}</div>
              </div>
            </div>
            
            <audio 
              key={`audio-${isRadioMode}-${currentQuality}`}
              ref={mediaRef}
              src={mediaUrl}
              controls
              className="audio-element"
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
            >
              Your browser does not support the audio tag.
            </audio>
            
            <div className="audio-controls-extra">
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
