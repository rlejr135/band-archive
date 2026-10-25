import { useState } from 'react';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import { MediaEngagement } from '../MediaEngagement.jsx';

export const PlaybackMedia = ({ media, band, api, workspaceKey, onMediaUpdate, onAuthoritativeRefresh, onDeleted, onStale }) => {
  const playback = media.playback || {};
  const videoSource = playback.video_url || (media.file_type === 'video' ? playback.url : null);
  const audioSource = playback.audio_url || (media.file_type === 'audio' ? playback.url : null);
  const [mode, setMode] = useState(videoSource ? 'video' : 'audio');
  const [failed, setFailed] = useState(false);
  const source = mode === 'video' ? videoSource : audioSource;
  const mediaName = media.display_name || `${media.file_type === 'video' ? '영상' : media.file_type === 'audio' ? '음원' : '파일'} #${media.id}`;
  const stale = () => { setFailed(true); onStale(); };
  return <section className="workspace-media"><div className="detail-heading"><strong>{mediaName}</strong><BandDeleteAction band={band} workspaceKey={`${workspaceKey}:media:${media.id}`} canDelete={media.can_delete} label="미디어" path={bandWorkspacePaths.media(band.id, media.id)} asynchronous resourceType="media" onDeleted={onDeleted} /></div>
    {videoSource && audioSource && <div className="media-modes"><button className={mode === 'video' ? 'active' : ''} onClick={() => { setFailed(false); setMode('video'); }}>영상</button><button className={mode === 'audio' ? 'active' : ''} onClick={() => { setFailed(false); setMode('audio'); }}>음원만 듣기</button></div>}
    {source ? mode === 'video' ? <video controls preload="metadata" src={source} onError={stale}>브라우저가 영상을 지원하지 않습니다.</video> : <audio controls preload="metadata" src={source} onError={stale}>브라우저가 오디오를 지원하지 않습니다.</audio> : <p className="workspace-muted">현재 재생 가능한 파일이 없습니다.</p>}
    {failed && <p className="workspace-muted">재생 주소를 한 번 새로고침했습니다. 계속 실패하면 잠시 후 다시 시도해 주세요.</p>}<MediaEngagement band={band} api={api} media={media} workspaceKey={workspaceKey} onMediaUpdate={onMediaUpdate} onAuthoritativeRefresh={onAuthoritativeRefresh} /></section>;
};
