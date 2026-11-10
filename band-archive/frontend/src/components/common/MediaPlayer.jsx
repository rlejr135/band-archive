import React, { useCallback, useEffect, useRef, useState } from 'react';
import { getMediaType } from '../../services/mediaPresentation';
import { getCommentTimestampSeekPlan } from '../../services/commentTimestamp';
import { captureModeTransition, clampPracticeMarkers, clampPracticeTime, consumeModeTransition, createPracticePlaybackState, formatPracticeTime, getPracticeLoopDecision, normalizePracticeRate, PRACTICE_PLAYBACK_RATES, validatePracticeLoop } from '../../services/practicePlayback';
import './MediaPlayer.css';

// Playback owns only media-element state. Upload subscriptions, processing
// requests, mutations and signed-URL refreshes belong to the screen wrapper.
const MediaPlayerInstance = ({ file, playbackIdentity, onPlaybackController, onPlaybackError, onRefresh, showTitle = true, preload = 'metadata' }) => {
  const sourceKey = playbackIdentity;
  const initialPracticeState = createPracticePlaybackState();
  const [isPlaying, setIsPlaying] = useState(initialPracticeState.isPlaying);
  const [isRadioMode, setIsRadioMode] = useState(() => getMediaType(file || {}) === 'video' && !file?.url && Boolean(file?.audio_url));
  const [currentTime, setCurrentTime] = useState(initialPracticeState.currentTime);
  const [duration, setDuration] = useState(initialPracticeState.duration);
  const [playbackRate, setPlaybackRate] = useState(initialPracticeState.playbackRate);
  const [markers, setMarkers] = useState(initialPracticeState.markers);
  const [loopEnabled, setLoopEnabled] = useState(initialPracticeState.loopEnabled);
  const [practiceStatus, setPracticeStatus] = useState('');
  const mediaRef = useRef(null);
  const transitionRef = useRef(null);
  const pendingSeekRef = useRef(null);
  const markersRef = useRef(markers);
  const loopRef = useRef(loopEnabled);
  const durationRef = useRef(duration);
  const rateRef = useRef(playbackRate);
  const attachMedia = useCallback((node) => {
    const previous = mediaRef.current;
    if (!node && previous) {
      // The URL may have expired while this content remains selected. Capture
      // before replacing the element, just as for a video/audio mode switch.
      transitionRef.current = captureModeTransition(transitionRef.current, {
        time: previous.currentTime, playing: !previous.paused, volume: previous.volume,
        rate: rateRef.current, markers: markersRef.current, loopEnabled: loopRef.current,
      }, sourceKey);
      if (!previous.paused) previous.pause();
    }
    mediaRef.current = node;
  }, [sourceKey]);

  useEffect(() => { markersRef.current = markers; }, [markers]);
  useEffect(() => { loopRef.current = loopEnabled; }, [loopEnabled]);
  useEffect(() => { durationRef.current = duration; }, [duration]);
  useEffect(() => { rateRef.current = playbackRate; }, [playbackRate]);
  useEffect(() => {
    if (!onPlaybackController) return undefined;
    onPlaybackController({
      getCurrentTime: () => {
        const time = mediaRef.current?.currentTime;
        return Number.isFinite(time) && time >= 0 ? time : null;
      },
      seekTo: (value) => {
        const media = mediaRef.current;
        const mediaDuration = media?.duration;
        const plan = getCommentTimestampSeekPlan(value, mediaDuration);
        if (!plan.accepted) return false;
        if (plan.pending || !media) {
          pendingSeekRef.current = plan.time;
          return true;
        }
        media.currentTime = plan.time;
        setCurrentTime(plan.time);
        return true;
      },
    });
    return () => onPlaybackController(null);
  }, [onPlaybackController]);

  if (!file) return null;
  const mediaType = getMediaType(file);
  const isVideo = mediaType === 'video';
  const isAudio = mediaType === 'audio';
  const isImage = mediaType === 'image';
  const isDocument = mediaType === 'document';
  const videoUrl = isVideo ? file.url : null;
  const audioUrl = file.audio_url || (isAudio ? file.url : null);
  const radioReady = isVideo && Boolean(videoUrl && audioUrl);
  const radioModeActive = isVideo && Boolean(audioUrl) && (isRadioMode || !videoUrl);
  const mediaUrl = radioModeActive || isAudio ? audioUrl : file.url;
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
    if (!restore) {
      const pending = pendingSeekRef.current;
      if (pending !== null && Number.isFinite(durationRef.current)) {
        const nextTime = clampPracticeTime(pending, durationRef.current);
        media.currentTime = nextTime;
        setCurrentTime(nextTime);
        pendingSeekRef.current = null;
      }
      return;
    }
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
    if (pendingSeekRef.current !== null && Number.isFinite(nextDuration)) {
      const pendingTime = clampPracticeTime(pendingSeekRef.current, nextDuration);
      media.currentTime = pendingTime;
      setCurrentTime(pendingTime);
      pendingSeekRef.current = null;
    }
    if (restore.playing) resumePlayback(media);
  };

  const resumePlayback = (media) => {
    // A pending play promise from an older mode must not update the new one.
    try {
      Promise.resolve(media.play()).then(() => {
        if (mediaRef.current === media) setIsPlaying(true);
      }).catch(() => {
        if (mediaRef.current !== media) return;
        setIsPlaying(false);
        setPracticeStatus('브라우저가 자동 재생을 허용하지 않았습니다. 재생 버튼을 눌러주세요.');
      });
    } catch {
      if (mediaRef.current !== media) return;
      setIsPlaying(false);
      setPracticeStatus('브라우저가 자동 재생을 허용하지 않았습니다. 재생 버튼을 눌러주세요.');
    }
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
    if (media && decision.shouldLoop) { media.currentTime = decision.seekTime; resumePlayback(media); return; }
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
  const mediaProps = { ref: attachMedia, src: mediaUrl, controls: true, onPlay: () => setIsPlaying(true), onPause: () => setIsPlaying(false), onLoadedMetadata: (event) => { if (event.currentTarget === mediaRef.current) restorePlayback(); }, onDurationChange: syncDuration, onTimeUpdate: handleTimeUpdate, onEnded: handleEnded, onRateChange: (event) => { const nextRate = normalizePracticeRate(event.currentTarget.playbackRate); rateRef.current = nextRate; setPlaybackRate(nextRate); } };
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

  return <div className="media-player"><div className="player-header">{showTitle && <div className="player-info"><span className="player-icon">{radioModeActive ? '📻' : (isVideo ? '🎬' : (isAudio ? '🎵' : (isImage ? '🖼️' : '📄')))}</span><span className="player-title">{file.name}</span></div>}{radioReady && <div className="media-mode-controls media-modes" role="group" aria-label="재생 모드"><button type="button" className={!radioModeActive ? 'selected' : ''} onClick={() => changeMode(false)}>영상</button><button type="button" className={radioModeActive ? 'selected' : ''} onClick={() => changeMode(true)}>음원만 듣기</button></div>}</div><div className="player-content">
    {!radioModeActive && isVideo && mediaUrl && <><video key={`video-${mediaUrl}`} {...mediaProps} preload={preload} onError={onPlaybackError} className="video-player">브라우저가 영상을 지원하지 않습니다.</video>{practiceControls}</>}
    {(radioModeActive || isAudio) && mediaUrl && <div className="audio-player-container"><div className="audio-visualizer"><div className="album-art">{radioModeActive ? '📻' : '🎵'}</div><div className="audio-info"><div className="audio-mode-badge">{radioModeActive ? 'RADIO MODE' : 'AUDIO'}</div>{showTitle && <div className="audio-filename">{file.name}</div>}</div></div><audio key={`audio-${mediaUrl}`} {...mediaProps} preload={preload} onError={onPlaybackError} className="audio-element">브라우저가 오디오를 지원하지 않습니다.</audio>{practiceControls}</div>}
    {isVideo && processing && <div className="media-processing" role="status"><p>{file.transcoding_status === 'processing' ? '음원을 추출 중입니다. 완료되면 음원만 들을 수 있습니다.' : '업로드 완료 · 음원 대기 중입니다.'}</p>{onRefresh && <button type="button" className="secondary-btn" onClick={onRefresh}>재생 정보 새로고침</button>}</div>}
    {isVideo && file.transcoding_status === 'failed' && <div className="media-processing error" role="alert"><p>음원 추출에 실패했습니다: {file.transcoding_error || '서버 오류'}</p>{onRefresh && <button type="button" onClick={onRefresh}>재생 정보 새로고침</button>}</div>}
    {isImage && mediaUrl && <div className="image-preview"><img src={mediaUrl} alt={file.name} className="preview-image" /></div>}{isDocument && mediaUrl && <div className="document-preview"><a href={mediaUrl} target="_blank" rel="noreferrer" className="document-link">📄 {file.name} 다운로드</a></div>}{!mediaUrl && <div className="no-preview"><p>재생 가능한 파일이 없습니다.</p></div>}
  </div></div>;
};

const MediaPlayer = (props) => {
  const identity = props.playbackIdentity ?? props.file?.id ?? props.file?.url ?? `${props.file?.name || ''}:${props.file?.audio_url || ''}`;
  return <MediaPlayerInstance key={identity} {...props} playbackIdentity={identity} />;
};

export default MediaPlayer;
