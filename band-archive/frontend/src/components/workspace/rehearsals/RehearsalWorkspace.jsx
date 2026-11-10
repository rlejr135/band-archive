import { isActiveBandMembership } from '../../../services/bandMembership.js';
import { useEffect, useId, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import RehearsalTypeBadge from '../../calendar/RehearsalTypeBadge.jsx';
import { RehearsalTabs } from '../AvailabilityCalendar.jsx';
import { ErrorState } from '../WorkspaceFeedback.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { useCollection } from '../useCollection.js';
import { WriteGuidance } from '../WorkspaceWriteGuidance.jsx';
import { useQueryDetail } from '../useQueryDetail.js';
import { RehearsalDetail } from './RehearsalDetail.jsx';
import { RehearsalForm } from './RehearsalForm.jsx';
import { RehearsalCreateDialog } from './RehearsalCreateDialog.jsx';
import './RehearsalWorkspace.css';

const extractDetail = (response) => response?.rehearsal;

export const RehearsalWorkspace = () => {
  const { band, workspaceKey } = useWorkspace();
  return <><RehearsalTabs band={band} /><RehearsalContentWorkspace key={`${workspaceKey}:${band.id}:rehearsals`} /></>;
};

const RehearsalContentWorkspace = () => {
  const { band, workspaceKey } = useWorkspace();
  const { loading, error, entries, pagination, refresh, loadMore } = useCollection('rehearsals');
  const [searchParams, setSearchParams] = useSearchParams();
  const [selected, setSelected] = useState(null);
  const [formMode, setFormMode] = useState(() => searchParams.get('create') === '1' && isActiveBandMembership(band.membership) ? 'create' : null);
  const [createPresentation, setCreatePresentation] = useState(() => typeof window !== 'undefined' && window.matchMedia?.('(max-width: 760px)').matches ? 'modal' : 'pane');
  const [createSaving, setCreateSaving] = useState(false);
  const createSavingRef = useRef(false);
  const createTrigger = useRef(null);
  const headerCreate = useRef(null);
  const detailValue = searchParams.get('rehearsal');
  const queryDetail = useQueryDetail({
    identity: workspaceKey, detailValue,
    pathForId: (id) => bandWorkspacePaths.rehearsal(band.id, id),
    extract: extractDetail,
  });
  const member = isActiveBandMembership(band.membership);
  const label = '합주';
  const detailPanelId = useId();
  const [mobile, setMobile] = useState(() => typeof window !== 'undefined' && Boolean(window.matchMedia?.('(max-width: 760px)').matches));
  useEffect(() => {
    const media = window.matchMedia?.('(max-width: 760px)');
    if (!media) return undefined;
    const update = () => setMobile(media.matches);
    update(); media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  const activeItem = detailValue == null ? selected : (!queryDetail.loading ? queryDetail.item : null);
  const clearDetailQuery = () => {
    const next = new URLSearchParams(searchParams);
    next.delete('rehearsal');
    ['create', 'date', 'period', 'availability'].forEach((key) => next.delete(key));
    setSearchParams(next, { replace: true });
  };
  const selectEntry = (entry) => { setSelected(mobile && activeItem?.id === entry.id ? null : entry); setFormMode(null); if (detailValue != null) clearDetailQuery(); };
  const startCreate = (event) => { createTrigger.current = event?.currentTarget || headerCreate.current; setCreatePresentation(mobile ? 'modal' : 'pane'); setSelected(null); setFormMode('create'); if (detailValue != null) clearDetailQuery(); };
  const deleteActive = () => { setSelected(null); setFormMode(null); if (detailValue != null) clearDetailQuery(); refresh(); };
  const finishEdit = () => { createSavingRef.current = false; setCreateSaving(false); setFormMode(null); setSelected(null); if (detailValue != null || searchParams.has('create')) clearDetailQuery(); refresh(); };
  const cancelEdit = () => { if (createSavingRef.current) return; setFormMode(null); if (searchParams.has('create')) { clearDetailQuery(); refresh(); } };
  const createDialogOpen = formMode === 'create' && createPresentation === 'modal';
  const reportSaving = (value) => { createSavingRef.current = value; setCreateSaving(value); };
  const inlineDetail = mobile && activeItem && entries.some((entry) => entry.id === activeItem.id) && formMode !== 'create';
  const detailPanel = <div className="workspace-detail" id={detailPanelId} role="region" aria-label="합주 상세">{formMode && !createDialogOpen ? <RehearsalForm key={`rehearsals:${formMode}:${formMode === 'edit' ? activeItem?.id ?? '' : 'new'}`} item={formMode === 'edit' ? activeItem : null} onDone={finishEdit} onCancel={cancelEdit} /> : detailValue != null ? queryDetail.loading ? <p aria-live="polite">{label} 정보를 불러오는 중입니다…</p> : queryDetail.error ? <ErrorState message={queryDetail.error} /> : activeItem ? <RehearsalDetail item={activeItem} onEdit={() => setFormMode('edit')} onDeleted={deleteActive} /> : <p className="workspace-placeholder">항목을 불러오지 못했습니다.</p> : activeItem ? <RehearsalDetail item={activeItem} onEdit={() => setFormMode('edit')} onDeleted={deleteActive} /> : <p className="workspace-placeholder">항목을 선택하면 상세 정보와 재생 미디어를 볼 수 있습니다.</p>}</div>;
  return <section className="workspace-page content-workspace rehearsal-workspace" aria-labelledby="rehearsals-title">
    <div className="workspace-page-heading"><div><h1 id="rehearsals-title">{label} 목록</h1><p>{member ? `${label}을 관리하고 재생 미디어를 확인하세요.` : `공개된 ${label}만 표시합니다.`}</p></div>{member && <button ref={headerCreate} className="primary-btn" onClick={startCreate}>{label} 추가</button>}</div>
    {!member && <WriteGuidance />}
    {loading && entries.length === 0 ? <p aria-live="polite">{label}을 불러오는 중입니다…</p> : error ? <ErrorState message={error} retry={refresh} /> : entries.length === 0 && !formMode && detailValue == null ? <div className="workspace-empty"><p>표시할 {label}이 없습니다.</p>{member && <button className="secondary-btn" onClick={startCreate}>첫 {label} 추가</button>}</div> : <div className="workspace-grid"><div className="workspace-list"><ul>{entries.map((entry) => <li key={entry.id}><button className={activeItem?.id === entry.id ? 'workspace-row selected' : 'workspace-row'} aria-expanded={activeItem?.id === entry.id} aria-controls={activeItem?.id === entry.id ? detailPanelId : undefined} onClick={() => selectEntry(entry)}><strong>{entry.title}</strong><span>{entry.date}</span><span className="rehearsal-row-meta"><RehearsalTypeBadge type={entry.rehearsal_type} /><small>{entry.effective_visibility === 'MEMBERS' ? '멤버 전용' : '공개'} · {entry.status || (entry.songs?.length ? `${entry.songs.length}곡` : '')}</small></span></button>{inlineDetail && activeItem.id === entry.id && <div className="rehearsal-inline-detail">{detailPanel}</div>}</li>)}</ul>{pagination?.has_more && <button className="secondary-btn" disabled={loading} onClick={loadMore}>{loading ? '불러오는 중…' : '더 불러오기'}</button>}</div>
      {!inlineDetail && detailPanel}</div>}
    {createDialogOpen && <RehearsalCreateDialog saving={createSaving} onClose={cancelEdit} returnFocusRef={createTrigger} fallbackFocusRef={headerCreate}><RehearsalForm onDone={finishEdit} onCancel={cancelEdit} onSavingChange={reportSaving} /></RehearsalCreateDialog>}
  </section>;
};
