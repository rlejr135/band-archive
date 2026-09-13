import React, { useEffect, useMemo, useRef, useState } from 'react';
import useMediaUpload from '../../hooks/useMediaUpload';
import { getMediaType } from '../../services/mediaPresentation';
import { captureModeTransition, clampPracticeMarkers, clampPracticeTime, consumeModeTransition, createPracticePlaybackState, formatPracticeTime, getPracticeLoopDecision, normalizePracticeRate, PRACTICE_PLAYBACK_RATES, validatePracticeLoop } from '../../services/practicePlayback';
import './MediaPlayer.css';

const MediaPlayerInstance = ({ file, onMediaUpdate, mediaKind = 'media' }) => {
  const sourceKey = file?.id || file?.url || `${file?.name || ''}:${file?.audio_url || ''}`;
  const initialPracticeState = createPracticePlaybackState();
  const [isPlaying, setIsPlaying] = useState(initialPracticeState.isPlaying);
  const [isRadioMode, setIsRadioMode] = useState(initialPracticeState.isRadioMode);
  const [currentTime, setCurrentTime] = useState(initialPracticeState.currentTime);
  const [duration, setDuration] = useState(initialPracticeState.duration);
  const [playbackRate, setPlaybackRate] = useState(initialPracticeState.playbackRate);
  const [markers, setMarkers] = useState(initialPracticeState.markers);
  const [loopEnabled, setLoopEnabled] = useState(initialPracticeState.loopEnabled);
  const [practiceStatus, setPracticeStatus] = useState('');
  const mediaRef = useRef(null);
  const transitionRef = useRef(null);
  const markersRef = useRef(markers);
  const loopRef = useRef(loopEnabled);
  const durationRef = useRef(duration);
  const rateRef = useRef(playbackRate);
  const onMediaUpdateRef = useRef(onMediaUpdate);
  const { retryAudio, watch } = useMediaUpload();
  const watchMedia = useMemo(() => ({ id: file?.id, transcoding_status: file?.transcoding_status }), [file?.id, file?.transcoding_status]);

  useEffect(() => { onMediaUpdateRef.current = onMediaUpdate; }, [onMediaUpdate]);
  useEffect(() => { markersRef.current = markers; }, [markers]);
  useEffect(() => { loopRef.current = loopEnabled; }, [loopEnabled]);
  useEffect(() => { durationRef.current = duration; }, [duration]);
  useEffect(() => { rateRef.current = playbackRate; }, [playbackRate]);

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
  const loopValidation = validatePracticeLoop({ ...markers, duration });

  const syncDuration = () => {
    const media = mediaRef.current;
    if (!media) return;
    const nextDuration = Number.isFinite(media.duration) && media.duration > 0 ? media.duration : Number.NaN;
    durationRef.current = nextDuration;
    setDuration(nextDuration);
    if (!Number.isFinite(nextDuration)) {
      if (loopRef.current) { loopRef.current = false; setLoopEnabled(false); setPracticeStatus('재생 길이를 확인할 수 없어 구간 반복을 끕니다.'); }
      return;
    }
    const nextMarkers = clampPracticeMarkers(markersRef.current, nextDuration);
    markersRef.current = nextMarkers;
    setMarkers(nextMarkers);
    const validation = validatePracticeLoop({ ...nextMarkers, duration: nextDuration });
    if (loopRef.current && !validation.valid) { loopRef.current = false; setLoopEnabled(false); setPracticeStatus(validation.reason); }
  };

  const restorePlayback = () => {
    const restore = consumeModeTransition(transitionRef.current, sourceKey);
    const media = mediaRef.current;
    if (!media) return;
    syncDuration();
    if (!restore) return;
    const nextDuration = durationRef.current;
    const nextTime = clampPracticeTime(restore.time, nextDuration);
    const nextRate = normalizePracticeRate(restore.rate);
    const nextMarkers = Number.isFinite(nextDuration) ? clampPracticeMarkers(restore.markers, nextDuration) : restore.markers;
    const validation = validatePracticeLoop({ ...nextMarkers, duration: nextDuration });
    media.currentTime = nextTime;
    media.volume = restore.volume;
    media.playbackRate = nextRate;
    setCurrentTime(nextTime); rateRef.current = nextRate; setPlaybackRate(nextRate);
    markersRef.current = nextMarkers; setMarkers(nextMarkers);
    loopRef.current = restore.loopEnabled && validation.valid; setLoopEnabled(loopRef.current);
    if (restore.loopEnabled && !validation.valid) setPracticeStatus(validation.reason);
    transitionRef.current = null;
    if (restore.playing) media.play().then(() => setIsPlaying(true)).catch(() => { setIsPlaying(false); setPracticeStatus('브라우저가 자동 재생을 허용하지 않았습니다. 재생 버튼을 눌러주세요.'); });
  };

  const handleTimeUpdate = () => {
    const media = mediaRef.current;
    if (!media) return;
    const time = Number.isFinite(media.currentTime) ? media.currentTime : 0;
    setCurrentTime(time);
    const decision = getPracticeLoopDecision({ time, ...markersRef.current, duration: durationRef.current, enabled: loopRef.current });
    if (decision.shouldLoop) {
      media.currentTime = decision.seekTime;
      setCurrentTime(decision.seekTime);
      setPracticeStatus(`A–B 반복 중: ${formatPracticeTime(decision.validation.start)}–${formatPracticeTime(decision.validation.end)}`);
    }
  };

  const handleEnded = () => {
    const media = mediaRef.current;
    const decision = getPracticeLoopDecision({ time: durationRef.current, ...markersRef.current, duration: durationRef.current, enabled: loopRef.current });
    if (media && decision.shouldLoop) { media.currentTime = decision.seekTime; media.play().catch(() => setIsPlaying(false)); return; }
    setIsPlaying(false);
  };

  const seekBy = (seconds) => {
    const media = mediaRef.current;
    if (!media || !Number.isFinite(durationRef.current)) return;
    const nextTime = clampPracticeTime(media.currentTime + seconds, durationRef.current);
    media.currentTime = nextTime; setCurrentTime(nextTime);
  };
  const setMarker = (marker) => {
    if (!Number.isFinite(durationRef.current)) { setPracticeStatus('재생 길이를 확인한 뒤 A–B 구간을 설정하세요.'); return; }
    const nextMarkers = { ...markersRef.current, [marker]: clampPracticeTime(mediaRef.current?.currentTime, durationRef.current) };
    const validation = validatePracticeLoop({ ...nextMarkers, duration: durationRef.current });
    markersRef.current = nextMarkers; setMarkers(nextMarkers);
    if (loopRef.current && !validation.valid) { loopRef.current = false; setLoopEnabled(false); }
    setPracticeStatus(validation.valid ? `${marker === 'start' ? 'A 시작' : 'B 끝'}을 ${formatPracticeTime(nextMarkers[marker])}에 설정했습니다.` : validation.reason);
  };
  const toggleLoop = () => {
    const validation = validatePracticeLoop({ ...markersRef.current, duration: durationRef.current });
    if (!validation.valid) { setPracticeStatus(validation.reason); return; }
    const nextEnabled = !loopRef.current;
    loopRef.current = nextEnabled; setLoopEnabled(nextEnabled);
    setPracticeStatus(nextEnabled ? `A–B 반복: ${formatPracticeTime(validation.start)}–${formatPracticeTime(validation.end)}` : 'A–B 반복을 껐습니다.');
  };
  const clearPracticeLoop = () => { markersRef.current = { start: null, end: null }; loopRef.current = false; setMarkers(markersRef.current); setLoopEnabled(false); setPracticeStatus('A–B 구간을 지웠습니다.'); };
  const changeRate = (value) => { const nextRate = normalizePracticeRate(value); if (mediaRef.current) mediaRef.current.playbackRate = nextRate; rateRef.current = nextRate; setPlaybackRate(nextRate); setPracticeStatus(`재생 속도 ${nextRate}배`); };
  const changeMode = (radio) => {
    const media = mediaRef.current;
    if (radio === isRadioMode) return;
    if (media) transitionRef.current = captureModeTransition(transitionRef.current, { time: media.currentTime, playing: !media.paused, volume: media.volume, rate: rateRef.current, markers: markersRef.current, loopEnabled: loopRef.current }, sourceKey);
    setIsRadioMode(radio);
  };
  const handleRetry = async () => { try { await retryAudio(file.id, () => {}, onMediaUpdate, mediaKind); } catch (error) { window.alert(error.message || '음원 추출 재시도에 실패했습니다.'); } };

  const mediaProps = { ref: mediaRef, src: mediaUrl, controls: true, onPlay: () => setIsPlaying(true), onPause: () => setIsPlaying(false), onLoadedMetadata: restorePlayback, onDurationChange: syncDuration, onTimeUpdate: handleTimeUpdate, onEnded: handleEnded, onRateChange: (event) => { const nextRate = normalizePracticeRate(event.currentTarget.playbackRate); rateRef.current = nextRate; setPlaybackRate(nextRate); } };
  const practiceControls = (isVideo || isAudio) && mediaUrl && <div className="practice-controls" role="group" aria-label="연습 재생 도구">
    <div className="practice-time" aria-live="polite">{formatPracticeTime(currentTime)} / {Number.isFinite(duration) ? formatPracticeTime(duration) : '--:--'}</div>
    <div className="practice-actions">
      <button type="button" onClick={() => seekBy(-5)} disabled={!Number.isFinite(duration)} aria-label="5초 뒤로 이동">−5초</button><button type="button" onClick={() => seekBy(5)} disabled={!Number.isFinite(duration)} aria-label="5초 앞으로 이동">+5초</button>
      <label className="practice-rate-label">속도 <select value={playbackRate} onChange={(event) => changeRate(event.target.value)} aria-label="재생 속도">{PRACTICE_PLAYBACK_RATES.map((rate) => <option key={rate} value={rate}>{rate}배</option>)}</select></label>
      <button type="button" onClick={() => setMarker('start')} disabled={!Number.isFinite(duration)} aria-label="현재 위치를 A 시작으로 설정">A {markers.start === null ? '시작' : formatPracticeTime(markers.start)}</button><button type="button" onClick={() => setMarker('end')} disabled={!Number.isFinite(duration)} aria-label="현재 위치를 B 끝으로 설정">B {markers.end === null ? '끝' : formatPracticeTime(markers.end)}</button>
      <button type="button" onClick={toggleLoop} disabled={!loopValidation.valid} aria-pressed={loopEnabled} aria-label="A-B 구간 반복">{loopEnabled ? '반복 켬' : '반복'}</button><button type="button" onClick={clearPracticeLoop} disabled={markers.start === null && markers.end === null} aria-label="A-B 구간 지우기">지우기</button>
    </div>
    <p className="practice-status" role="status">{practiceStatus || (loopValidation.valid ? `A–B ${formatPracticeTime(loopValidation.start)}–${formatPracticeTime(loopValidation.end)} 준비됨` : loopValidation.reason) || (isPlaying ? '재생 중' : '일시정지')}</p>
  </div>;

  return <div className="media-player"><div className="player-header"><div className="player-info"><span className="player-icon">{radioModeActive ? '📻' : (isVideo ? '🎬' : (isAudio ? '🎵' : (isImage ? '🖼️' : '📄')))}</span><span className="player-title">{file.name}</span></div>{isVideo && <div className="media-mode-controls" role="group" aria-label="재생 모드"><button type="button" className={!radioModeActive ? 'selected' : ''} onClick={() => changeMode(false)}>영상</button><button type="button" className={radioModeActive ? 'selected' : ''} onClick={() => changeMode(true)} disabled={!radioReady}>라디오 (데이터 절약)</button></div>}</div><div className="player-content">
    {!radioModeActive && isVideo && mediaUrl && <><video key={`video-${sourceKey}`} {...mediaProps} className="video-player">Your browser does not support the video tag.</video>{practiceControls}</>}
    {(radioModeActive || isAudio) && mediaUrl && <div className="audio-player-container"><div className="audio-visualizer"><div className="album-art">{radioModeActive ? '📻' : '🎵'}</div><div className="audio-info"><div className="audio-mode-badge">{radioModeActive ? 'RADIO MODE' : 'AUDIO'}</div><div className="audio-filename">{file.name}</div></div></div><audio key={`audio-${sourceKey}-${radioModeActive}`} {...mediaProps} className="audio-element">Your browser does not support the audio tag.</audio>{practiceControls}</div>}
    {isVideo && processing && <p className="media-processing" role="status">{file.transcoding_status === 'processing' ? '음원을 추출 중입니다. 완료되면 라디오 모드를 사용할 수 있습니다.' : '업로드 완료 · 음원 대기 중입니다.'}</p>}
    {isVideo && file.transcoding_status === 'failed' && <div className="media-processing error" role="alert"><p>음원 추출에 실패했습니다: {file.transcoding_error || '서버 오류'}</p><button type="button" onClick={handleRetry}>음원 추출 재시도</button></div>}
    {isImage && mediaUrl && <div className="image-preview"><img src={mediaUrl} alt={file.name} className="preview-image" /></div>}{isDocument && mediaUrl && <div className="document-preview"><a href={mediaUrl} target="_blank" rel="noreferrer" className="document-link">📄 {file.name} 다운로드</a></div>}{!mediaUrl && <div className="no-preview"><p>미리보기를 사용할 수 없습니다</p></div>}
  </div></div>;
};

const MediaPlayer = (props) => {
  const sourceKey = props.file?.id || props.file?.url || `${props.file?.name || ''}:${props.file?.audio_url || ''}`;
  return <MediaPlayerInstance key={sourceKey} {...props} />;
};

export default MediaPlayer;
