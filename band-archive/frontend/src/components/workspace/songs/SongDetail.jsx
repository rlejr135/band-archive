import { useCallback } from 'react';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import { BandUploadInput } from '../BandUploadInput.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { PAGE_SIZE } from '../workspaceReadSupport.js';
import { PlaybackMedia } from '../media/PlaybackMedia.jsx';
import { useDetailMedia } from '../media/useDetailMedia.js';

export const SongDetail = (props) => {
  const { band, workspaceKey } = useWorkspace();
  return <SongDetailContent key={`song:${workspaceKey}:${band.id}:${props.item.id}`} {...props} />;
};

const SongDetailContent = ({
  item,
  onEdit,
  onDeleted
}) => {
  const {
    band,
    api,
    workspaceKey
  } = useWorkspace();
  const mediaPath = useCallback(offset => bandWorkspacePaths.songMedia(band.id, item.id, {
    limit: PAGE_SIZE,
    offset
  }), [band.id, item.id]);
  const { mediaState, loadMedia, refreshAuthoritativeMedia, updateMedia, refreshStale } = useDetailMedia({
    api, workspaceKey, resourceId: item.id, mediaPath
  });
  const resourceLabel = '곡';
  return <article><div className="detail-heading"><div><h2>{item.title}</h2><p>{item.artist}</p></div>{item.can_edit && <button className="secondary-btn" onClick={onEdit}>수정</button>}<BandDeleteAction band={band} workspaceKey={workspaceKey} canDelete={item.can_delete} label={resourceLabel} path={bandWorkspacePaths.song(band.id, item.id)} onDeleted={onDeleted} /></div>
    <dl><dt>상태</dt><dd>{item.status || '-'}</dd><dt>장르</dt><dd>{item.genre || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl>
    {band.membership && <BandUploadInput band={band} songId={item.id} parentVisibility={item.effective_visibility || item.visibility} onMediaComplete={refreshAuthoritativeMedia} />}
    <h3>재생 미디어</h3>{mediaState.loading && mediaState.pages.length === 0 ? <p>미디어를 불러오는 중입니다…</p> : mediaState.error && mediaState.pages.length === 0 ? <p className="workspace-muted">{mediaState.error}</p> : mediaState.pages.length === 0 ? <p className="workspace-muted">표시할 미디어가 없습니다. 업로드는 준비 중입니다.</p> : <><div className="media-list">{mediaState.pages.flatMap(page => page.media.map(media => <PlaybackMedia key={media.id} media={media} band={band} api={api} workspaceKey={workspaceKey} onMediaUpdate={updateMedia} onAuthoritativeRefresh={refreshAuthoritativeMedia} onDeleted={refreshAuthoritativeMedia} onStale={() => {
          refreshStale(page.offset, media.id);
        }} />))}</div>{mediaState.pagination?.has_more && <button className="secondary-btn" disabled={mediaState.loading} onClick={() => loadMedia(mediaState.pagination.next_offset)}>{mediaState.loading ? '미디어를 불러오는 중…' : '미디어 더 불러오기'}</button>}{mediaState.error && <p className="workspace-muted" role="alert">{mediaState.error}</p>}</>}
  </article>;
};
