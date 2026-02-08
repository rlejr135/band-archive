import React, { useState, useRef } from 'react';
import './MediaPlayer.css';

const MediaPlayer = ({ file }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const mediaRef = useRef(null);

  if (!file) return null;

  const isVideo = file.type === 'video' || file.name?.match(/\.(mp4|webm|ogg|mov|avi|mkv)$/i);
  const isAudio = file.type === 'audio' || file.name?.match(/\.(mp3|wav|ogg|m4a|aac|flac)$/i);
  
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
        <span className="player-icon">{isVideo ? '🎬' : '🎵'}</span>
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
