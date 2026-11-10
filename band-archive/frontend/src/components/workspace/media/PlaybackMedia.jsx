import { useState } from 'react';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { workspaceMediaFile } from '../../../services/playbackMedia.js';
import MediaPlayer from '../../common/MediaPlayer.jsx';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import { MediaEngagement } from '../MediaEngagement.jsx';

export const PlaybackMedia = ({ media, band, api, workspaceKey, onMediaUpdate, onAuthoritativeRefresh, onDeleted, onStale }) => {
  const [failed, setFailed] = useState(false);
  const mediaName = media.display_name || `${media.file_type === 'video' ? '영상' : media.file_type === 'audio' ? '음원' : '파일'} #${media.id}`;
  const stale = () => { setFailed(true); onStale?.(); };
  return <section className="workspace-media"><div className="detail-heading"><strong>{mediaName}</strong><BandDeleteAction band={band} workspaceKey={`${workspaceKey}:media:${media.id}`} canDelete={media.can_delete} label="미디어" path={bandWorkspacePaths.media(band.id, media.id)} asynchronous resourceType="media" onDeleted={onDeleted} /></div>
    <MediaPlayer file={workspaceMediaFile(media)} playbackIdentity={`${workspaceKey}:band:${band.id}:media:${media.id}`} showTitle={false} onPlaybackError={stale} onRefresh={onAuthoritativeRefresh} />
    {failed && <p className="workspace-muted">재생 주소를 한 번 새로고침했습니다. 계속 실패하면 잠시 후 다시 시도해 주세요.</p>}<MediaEngagement band={band} api={api} media={media} workspaceKey={workspaceKey} onMediaUpdate={onMediaUpdate} onAuthoritativeRefresh={onAuthoritativeRefresh} /></section>;
};
