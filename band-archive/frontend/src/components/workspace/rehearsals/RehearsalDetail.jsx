import { useCallback } from 'react';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import RehearsalTypeBadge from '../../calendar/RehearsalTypeBadge.jsx';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { PAGE_SIZE } from '../workspaceReadSupport.js';
import { PlaybackMedia } from '../media/PlaybackMedia.jsx';
import { useDetailMedia } from '../media/useDetailMedia.js';

export const RehearsalDetail = (props) => {
  const { band, workspaceKey } = useWorkspace();
  return <RehearsalDetailContent key={`rehearsal:${workspaceKey}:${band.id}:${props.item.id}`} {...props} />;
};

const RehearsalDetailContent = ({ item, onEdit, onDeleted }) => {
  const { band, api, workspaceKey } = useWorkspace();
  const mediaPath = useCallback((offset) => bandWorkspacePaths.rehearsalMedia(band.id, item.id, { limit: PAGE_SIZE, offset }), [band.id, item.id]);
  const { mediaState, loadMedia, refreshAuthoritativeMedia, updateMedia, refreshStale } = useDetailMedia({
    api, workspaceKey, resourceId: item.id, mediaPath
  });
  return <article><div className="detail-heading"><div><h2>{item.title}</h2><p>{`${item.date}${item.time ? ` · ${item.time}` : ''}`}</p></div>{item.can_edit && <button className="secondary-btn" onClick={onEdit}>수정</button>}<BandDeleteAction band={band} workspaceKey={workspaceKey} canDelete={item.can_delete} label="합주" path={bandWorkspacePaths.rehearsal(band.id, item.id)} onDeleted={onDeleted} /></div>
    <dl><dt>합주 유형</dt><dd><RehearsalTypeBadge type={item.rehearsal_type} /></dd><dt>장소</dt><dd>{item.location || '-'}</dd><dt>연결된 곡</dt><dd>{item.songs?.map((song) => song.title).join(', ') || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl>
    <h3>재생 미디어</h3>{mediaState.loading && mediaState.pages.length === 0 ? <p>미디어를 불러오는 중입니다…</p> : mediaState.error && mediaState.pages.length === 0 ? <p className="workspace-muted">{mediaState.error}</p> : mediaState.pages.length === 0 ? <p className="workspace-muted">표시할 미디어가 없습니다. 업로드는 준비 중입니다.</p> : <><div className="media-list">{mediaState.pages.flatMap((page) => page.media.map((media) => <PlaybackMedia key={media.id} media={media} band={band} api={api} workspaceKey={workspaceKey} onMediaUpdate={updateMedia} onAuthoritativeRefresh={refreshAuthoritativeMedia} onDeleted={refreshAuthoritativeMedia} onStale={() => { refreshStale(page.offset, media.id); }} />))}</div>{mediaState.pagination?.has_more && <button className="secondary-btn" disabled={mediaState.loading} onClick={() => loadMedia(mediaState.pagination.next_offset)}>{mediaState.loading ? '미디어를 불러오는 중…' : '미디어 더 불러오기'}</button>}{mediaState.error && <p className="workspace-muted" role="alert">{mediaState.error}</p>}</>}
  </article>;
};
