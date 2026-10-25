/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { ErrorState } from '../WorkspaceFeedback.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { useCollection } from '../useCollection.js';
import { WriteGuidance } from '../WorkspaceWriteGuidance.jsx';
import { requestOptions } from '../workspaceReadSupport.js';
import { strictPositiveId } from '../bandDashboardState.js';
import { SongDetail } from './SongDetail.jsx';
import { SongForm } from './SongForm.jsx';

export const SongWorkspace = () => {
  const { band, workspaceKey } = useWorkspace();
  return <SongContentWorkspace key={`${workspaceKey}:${band.id}:songs`} />;
};

const SongContentWorkspace = () => {
  const {
    band,
    api,
    workspaceKey
  } = useWorkspace();
  const {
    loading,
    error,
    entries,
    pagination,
    refresh,
    loadMore
  } = useCollection('songs');
  const [searchParams, setSearchParams] = useSearchParams();
  const [selected, setSelected] = useState(null);
  const [formMode, setFormMode] = useState(() => null);
  const detailParam = 'song';
  const detailValue = searchParams.get(detailParam);
  const detailId = detailValue == null ? null : strictPositiveId(detailValue);
  const [queryDetail, setQueryDetail] = useState({
    id: null,
    loading: false,
    error: '',
    item: null
  });
  const detailEpoch = useRef(0);
  const member = Boolean(band.membership);
  const label = '곡';
  useEffect(() => {
    if (detailValue == null) {
      detailEpoch.current += 1;
      setQueryDetail({
        id: null,
        loading: false,
        error: '',
        item: null
      });
      return undefined;
    }
    const epoch = ++detailEpoch.current;
    if (!detailId) {
      setQueryDetail({
        id: null,
        loading: false,
        error: '요청한 항목을 찾을 수 없습니다.',
        item: null
      });
      return undefined;
    }
    const controller = new AbortController();
    setQueryDetail({
      id: detailId,
      loading: true,
      error: '',
      item: null
    });
    const resource = 'song';
    api.requestJson(bandWorkspacePaths[resource](band.id, detailId), requestOptions(controller.signal)).then(response => {
      const item = response?.[resource];
      const sameBand = item?.band_id == null || Number(item.band_id) === Number(band.id);
      if (controller.signal.aborted || epoch !== detailEpoch.current) return;
      if (!item || Number(item.id) !== detailId || !sameBand) {
        setQueryDetail({
          id: detailId,
          loading: false,
          error: '요청한 항목을 찾을 수 없습니다.',
          item: null
        });
        return;
      }
      setQueryDetail({
        id: detailId,
        loading: false,
        error: '',
        item
      });
    }).catch(() => {
      if (!controller.signal.aborted && epoch === detailEpoch.current) setQueryDetail({
        id: detailId,
        loading: false,
        error: '요청한 항목을 찾을 수 없습니다.',
        item: null
      });
    });
    return () => controller.abort();
  }, [api, band.id, detailId, detailValue, workspaceKey]);
  const activeItem = detailValue == null ? selected : queryDetail.id === detailId && !queryDetail.loading ? queryDetail.item : null;
  const clearDetailQuery = () => {
    const next = new URLSearchParams(searchParams);
    next.delete(detailParam);
    ['create', 'date', 'period', 'availability'].forEach(key => next.delete(key));
    setSearchParams(next, {
      replace: true
    });
  };
  const selectEntry = entry => {
    setSelected(entry);
    setFormMode(null);
    if (detailValue != null) clearDetailQuery();
  };
  const startCreate = () => {
    setSelected(null);
    setFormMode('create');
    if (detailValue != null) clearDetailQuery();
  };
  const deleteActive = () => {
    setSelected(null);
    setFormMode(null);
    if (detailValue != null) clearDetailQuery();
    refresh();
  };
  const finishEdit = () => {
    setFormMode(null);
    setSelected(null);
    if (detailValue != null || searchParams.has('create')) clearDetailQuery();
    refresh();
  };
  const cancelEdit = () => {
    setFormMode(null);
    if (searchParams.has('create')) {
      clearDetailQuery();
      refresh();
    }
  };
  return <section className="workspace-page content-workspace" aria-labelledby="songs-title">
    <div className="workspace-page-heading"><div><h1 id="songs-title">{label} 목록</h1><p>{member ? `${label}을 관리하고 재생 미디어를 확인하세요.` : `공개된 ${label}만 표시합니다.`}</p></div>{member && <button className="primary-btn" onClick={startCreate}>{label} 추가</button>}</div>
    {!member && <WriteGuidance />}
    {loading && entries.length === 0 ? <p aria-live="polite">{label}을 불러오는 중입니다…</p> : error ? <ErrorState message={error} retry={refresh} /> : entries.length === 0 && !formMode && detailValue == null ? <div className="workspace-empty"><p>표시할 {label}이 없습니다.</p>{member && <button className="secondary-btn" onClick={startCreate}>첫 {label} 추가</button>}</div> : <div className="workspace-grid"><div className="workspace-list"><ul>{entries.map(entry => <li key={entry.id}><button className={activeItem?.id === entry.id ? 'workspace-row selected' : 'workspace-row'} onClick={() => selectEntry(entry)}><strong>{entry.title}</strong><span>{entry.artist}</span><small>{entry.effective_visibility === 'MEMBERS' ? '멤버 전용' : '공개'} · {entry.status || (entry.songs?.length ? `${entry.songs.length}곡` : '')}</small></button></li>)}</ul>{pagination?.has_more && <button className="secondary-btn" disabled={loading} onClick={loadMore}>{loading ? '불러오는 중…' : '더 불러오기'}</button>}</div>
      <div className="workspace-detail">{formMode ? <SongForm key={`songs:${formMode}:${formMode === 'edit' ? activeItem?.id ?? '' : 'new'}`} item={formMode === 'edit' ? activeItem : null} onDone={finishEdit} onCancel={cancelEdit} /> : detailValue != null ? queryDetail.loading ? <p aria-live="polite">{label} 정보를 불러오는 중입니다…</p> : queryDetail.error ? <ErrorState message={queryDetail.error} /> : activeItem ? <SongDetail item={activeItem} onEdit={() => setFormMode('edit')} onDeleted={deleteActive} /> : <p className="workspace-placeholder">항목을 불러오지 못했습니다.</p> : activeItem ? <SongDetail item={activeItem} onEdit={() => setFormMode('edit')} onDeleted={deleteActive} /> : <p className="workspace-placeholder">항목을 선택하면 상세 정보와 재생 미디어를 볼 수 있습니다.</p>}</div></div>}
  </section>;
};
