import React, { useState, useRef, useEffect, useMemo } from 'react';
import useMediaUpload from '../../hooks/useMediaUpload';
import { getMediaType } from '../../services/mediaPresentation';
import './MediaPlayer.css';

const MediaPlayer = ({ file, onMediaUpdate, mediaKind = 'media' }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const [isRadioMode, setIsRadioMode] = useState(false);
  const mediaRef = useRef(null);
  const restoreRef = useRef(null);
  const onMediaUpdateRef = useRef(onMediaUpdate);
  const { retryAudio, watch } = useMediaUpload();
  const watchMedia = useMemo(() => ({ id: file?.id, transcoding_status: file?.transcoding_status }), [file?.id, file?.transcoding_status]);

  useEffect(() => { onMediaUpdateRef.current = onMediaUpdate; }, [onMediaUpdate]);
  const watchable = watchMedia.id && file?.type === 'video' && ['queued', 'pending', 'processing'].includes(watchMedia.transcoding_status);
  useEffect(() => {
    if (!watchable) return undefined;
    return watch(watchMedia, (media) => onMediaUpdateRef.current?.(media), mediaKind);
  }, [mediaKind, watch, watchMedia, watchable]);

  if (!file) return null;

  const mediaType = getMediaType(file);
  const isVideo = mediaType === 'video';
  const isAudio = mediaType === 'audio';
  const isImage = mediaType === 'image';
  const isDocument = mediaType === 'document';

  const radioReady = isVideo && file.transcoding_status === 'completed' && Boolean(file.audio_url);
  const radioModeActive = isRadioMode && radioReady;
  const mediaUrl = radioModeActive ? file.audio_url : file.url;
  const processing = ['queued', 'pending', 'processing'].includes(file.transcoding_status);

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

  const changeMode = (radio) => {
    if (mediaRef.current) {
      restoreRef.current = { time: mediaRef.current.currentTime, playing: !mediaRef.current.paused, volume: mediaRef.current.volume };
    }
    setIsRadioMode(radio);
  };

  const restorePlayback = () => {
    const restore = restoreRef.current;
    if (!restore || !mediaRef.current) return;
    mediaRef.current.currentTime = restore.time;
    mediaRef.current.volume = restore.volume;
    if (restore.playing) mediaRef.current.play().catch(() => {});
    restoreRef.current = null;
  };

  const handleRetry = async () => {
    try { await retryAudio(file.id, () => {}, onMediaUpdate, mediaKind); }
    catch (error) { window.alert(error.message || '음원 추출 재시도에 실패했습니다.'); }
  };

  return (
    <div className="media-player">
      <div className="player-header">
        <div className="player-info">
          <span className="player-icon">
            {radioModeActive ? '📻' : (isVideo ? '🎬' : (isAudio ? '🎵' : (isImage ? '🖼️' : '📄')))}
          </span>
          <span className="player-title">{file.name}</span>
        </div>
        
        {isVideo && <div className="media-mode-controls" role="group" aria-label="재생 모드"><button type="button" className={!radioModeActive ? 'selected' : ''} onClick={() => changeMode(false)}>영상</button><button type="button" className={radioModeActive ? 'selected' : ''} onClick={() => changeMode(true)} disabled={!radioReady}>라디오 (데이터 절약)</button></div>}
      </div>
      
      <div className="player-content">
        {!radioModeActive && isVideo && mediaUrl && (
          <video 
            key="video"
            ref={mediaRef}
            src={mediaUrl}
            controls
            className="video-player"
            onPlay={() => setIsPlaying(true)}
            onPause={() => setIsPlaying(false)}
            onLoadedMetadata={restorePlayback}
          >
            Your browser does not support the video tag.
          </video>
        )}
        
        {(radioModeActive || isAudio) && mediaUrl && (
          <div className="audio-player-container">
            <div className="audio-visualizer">
              <div className="album-art">
                {radioModeActive ? '📻' : '🎵'}
              </div>
              <div className="audio-info">
                <div className="audio-mode-badge">{radioModeActive ? 'RADIO MODE' : 'AUDIO'}</div>
                <div className="audio-filename">{file.name}</div>
              </div>
            </div>
            
            <audio 
              key={`audio-${radioModeActive}`}
              ref={mediaRef}
              src={mediaUrl}
              controls
              className="audio-element"
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
              onLoadedMetadata={restorePlayback}
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

        {isVideo && processing && <p className="media-processing" role="status">{file.transcoding_status === 'processing' ? '음원을 추출 중입니다. 완료되면 라디오 모드를 사용할 수 있습니다.' : '업로드 완료 · 음원 대기 중입니다.'}</p>}
        {isVideo && file.transcoding_status === 'failed' && <div className="media-processing error" role="alert"><p>음원 추출에 실패했습니다: {file.transcoding_error || '서버 오류'}</p><button type="button" onClick={handleRetry}>음원 추출 재시도</button></div>}

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
