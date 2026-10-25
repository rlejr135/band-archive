import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useServicePolicy } from '../../context/ServicePolicyContext.jsx';
import { reviewPublicVisibility } from '../../services/uploadPolicy.js';
import { useBands } from '../../context/BandContext.jsx';
import HeaderActions from '../layout/HeaderActions.jsx';
import BandActivityDashboard from '../dashboard/BandActivityDashboard.jsx';
import AvailabilityCalendar, { RehearsalTabs } from './AvailabilityCalendar.jsx';
import { availabilityFingerprint, availabilityPath, PERIOD_LABELS, validDay } from './availabilityState.js';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandWorkspacePaths, rehearsalPayload, songPayload } from '../../services/bandWorkspaceApi.js';
import { normalizeRehearsalType, REHEARSAL_TYPES } from '../../services/rehearsalTypes.js';
import { safeExternalHttpUrl } from '../../services/safeExternalUrl.js';
import BandPlacePicker from './BandPlacePicker.jsx';
import { MembersWorkspace } from './members/MembersWorkspace.jsx';
import { MemberDetailWorkspace } from './members/MemberDetailWorkspace.jsx';
import { BandDeleteAction } from './BandDeleteAction.jsx';
import RehearsalTypeBadge from '../calendar/RehearsalTypeBadge.jsx';
import { AnnouncementEditor } from './announcements/AnnouncementEditor.jsx';
import { FeaturedMediaEditor } from './featured-media/FeaturedMediaEditor.jsx';
import { RepresentativeMediaWorkspace } from './featured-media/FeaturedMediaWorkspace.jsx';
import { SuggestionsWorkspace } from './suggestions/SuggestionsWorkspace.jsx';
import { GalleryWorkspace } from './gallery/GalleryWorkspace.jsx';
import { BandUploadInput } from './BandUploadInput.jsx';
import { ErrorState } from './WorkspaceFeedback.jsx';
import { WorkspaceContext, useWorkspace } from './WorkspaceContext.jsx';
import { useCollection } from './useCollection.js';
import { WriteGuidance } from './WorkspaceWriteGuidance.jsx';
import { PAGE_SIZE, requestOptions } from './workspaceReadSupport.js';
import UploadCenter from '../common/UploadCenter.jsx';
import MediaPlayer from '../common/MediaPlayer.jsx';
import { createWorkspaceRequestGate, mergeMediaPage, takeOneStaleRefresh, WorkspacePaintGate, workspaceIdentityKey } from './workspaceRouteState.js';
import { strictPositiveId } from './bandDashboardState.js';
import './BandWorkspace.css';
import { SongWorkspace } from './songs/SongWorkspace.jsx';
import { PlaybackMedia } from './media/PlaybackMedia.jsx';

export { AnnouncementEditor };
export { FeaturedMediaEditor };
export { SuggestionsWorkspace };
export { GalleryWorkspace };
// Compatibility exports: remove after all consumers use the members entry points.
export { MembersWorkspace, MemberDetailWorkspace };
// Compatibility export retained for the frozen comparison fixture.
export { RepresentativeMediaWorkspace };
// Compatibility export: new consumers import the songs entry point directly.
export { SongWorkspace };

// This file intentionally starts request state from effects: each state reset
// prevents a previous band/selection from rendering while its replacement is
// being resolved. The guarded async callbacks then own the terminal state.
/* eslint-disable react-hooks/set-state-in-effect */

// React Router reuses a route element when only params change. Keying this
// boundary makes the old workspace (including open forms/details) unmount
// synchronously before the next slug or account can render.
export const BandWorkspaceRoute = () => {
  const { bandSlug } = useParams();
  const { user } = useAuth();
  const { membershipRevisions = {} } = useBands();
  return <BandWorkspace key={`${workspaceIdentityKey(bandSlug, user?.id)}:${membershipRevisions[bandSlug] || 0}`} />;
};

export const BandWorkspace = () => {
  const { bandSlug } = useParams();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { user, getAccessToken, refreshSession } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [state, setState] = useState({ loading: true, error: '', band: null });
  const requestGate = useRef(createWorkspaceRequestGate());
  const currentBandRequest = useRef({ routeKey: '', version: null });
  const logoRefreshes = useRef(new Set());
  const logoRefreshController = useRef(null);
  const [failedLogoKey, setFailedLogoKey] = useState('');
  const routeKey = workspaceIdentityKey(bandSlug, user?.id);

  useEffect(() => {
    if (!state.band || state.band.slug !== bandSlug) return undefined;
    document.title = `${state.band.name} · Band Archive`;
    return () => { document.title = 'Band Archive'; };
  }, [bandSlug, pathname, state.band, state.band?.name, state.band?.slug]);

  useEffect(() => {
    const controller = new AbortController();
    const version = requestGate.current.begin(routeKey);
    currentBandRequest.current = { routeKey, version };
    logoRefreshes.current.clear();
    setFailedLogoKey('');
    setState({ loading: true, error: '', band: null });
    api.requestJson(bandWorkspacePaths.bandBySlug(bandSlug), requestOptions(controller.signal))
      .then((response) => {
        if (controller.signal.aborted || !requestGate.current.accepts(routeKey, version) || !response?.band) return;
        setState({ loading: false, error: '', band: response.band });
      })
      .catch(() => {
        if (!controller.signal.aborted && requestGate.current.accepts(routeKey, version)) {
          setState({ loading: false, error: '밴드를 찾을 수 없거나 볼 권한이 없습니다.', band: null });
        }
      });
    return () => { controller.abort(); logoRefreshController.current?.abort(); logoRefreshController.current = null; };
  }, [api, bandSlug, routeKey]);

  const refreshLogo = async () => {
    const logoImage = state.band?.logo;
    const imageId = logoImage?.gallery_image_id;
    const refreshKey = `${routeKey}:${imageId ?? 'missing'}`;
    const failedKey = `${refreshKey}:${logoImage?.url || ''}`;
    if (!logoImage || imageId == null || !logoImage.url) return;
    if (logoRefreshes.current.has(refreshKey)) { setFailedLogoKey(failedKey); return; }
    logoRefreshes.current.add(refreshKey);
    const controller = new AbortController();
    logoRefreshController.current?.abort();
    logoRefreshController.current = controller;
    const activeRequest = currentBandRequest.current;
    try {
      const response = await api.requestJson(bandWorkspacePaths.bandBySlug(bandSlug), requestOptions(controller.signal));
      const refreshedBand = response?.band;
      const stillCurrent = !controller.signal.aborted
        && currentBandRequest.current === activeRequest
        && requestGate.current.accepts(routeKey, activeRequest.version)
        && refreshedBand?.slug === bandSlug
        && String(refreshedBand?.id) === String(state.band.id);
      if (!stillCurrent) return;
      const refreshedLogo = refreshedBand.logo || null;
      setState((previous) => previous.band && String(previous.band.id) === String(refreshedBand.id)
        ? { ...previous, band: { ...previous.band, logo: refreshedLogo } }
        : previous);
      if (String(refreshedLogo?.gallery_image_id) === String(imageId) && refreshedLogo?.url === logoImage.url) setFailedLogoKey(failedKey);
    } catch {
      if (!controller.signal.aborted && currentBandRequest.current === activeRequest) setFailedLogoKey(failedKey);
    } finally {
      if (logoRefreshController.current === controller) logoRefreshController.current = null;
    }
  };

  if (state.loading) return <WorkspacePaintGate requestedSlug={bandSlug} />;
  if (state.error || !state.band) return <main className="band-workspace-loading" role="alert"><p>{state.error}</p><button className="secondary-btn" onClick={() => navigate('/bands')}>내 밴드로 이동</button></main>;
  if (state.band.slug !== bandSlug) return <WorkspacePaintGate requestedSlug={bandSlug} resolvedSlug={state.band.slug} />;
  const value = { band: state.band, api, workspaceKey: `${state.band.id}:${user?.id || 'visitor'}` };
  const logoUrl = safeExternalHttpUrl(state.band.logo?.url);
  const logoFailed = failedLogoKey === `${routeKey}:${state.band.logo?.gallery_image_id ?? 'missing'}:${state.band.logo?.url || ''}`;
  return <WorkspaceContext.Provider value={value}>
    <div className="band-workspace">
      <header className="workspace-header">
        <button className="workspace-brand" onClick={() => navigate(`/bands/${encodeURIComponent(state.band.slug)}`)}>
          {logoUrl && !logoFailed ? <img src={logoUrl} alt={`${state.band.name} 로고`} onError={refreshLogo} /> : <span className="workspace-brand-mark" aria-hidden="true">♫</span>}<span>{state.band.name}<small> Archive</small></span>
        </button>
        <div className="workspace-actions">
          <UploadCenter />
          <HeaderActions currentSlug={state.band.slug} />
        </div>
        <nav aria-label="밴드 공간">
          <NavLink end to={`/bands/${encodeURIComponent(state.band.slug)}`}>대시보드</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/songs`}>곡</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/representative-media`}>대표 미디어</NavLink>
          <NavLink className={({ isActive }) => isActive || pathname.endsWith('/availability') ? 'active' : ''} to={`/bands/${encodeURIComponent(state.band.slug)}/rehearsals`}>합주</NavLink>
          {state.band.permissions?.can_view_roster && <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/members`}>멤버</NavLink>}
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/gallery`}>갤러리</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/suggestions`}>추천곡</NavLink>
          {state.band.permissions?.can_manage_members && state.band.permissions?.can_manage_invites && <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/manage`}>관리</NavLink>}
        </nav>
      </header>
      <main className="workspace-main"><Outlet /></main>
    </div>
  </WorkspaceContext.Provider>;
};

export const BandDashboard = () => {
  const { band, api, workspaceKey } = useWorkspace();
  return <BandActivityDashboard key={workspaceKey} band={band} api={api} workspaceKey={workspaceKey} />;
};

export const RehearsalWorkspace = () => {
  const { band, workspaceKey } = useWorkspace();
  return <><RehearsalTabs band={band} /><ContentWorkspace key={`${workspaceKey}:${band.id}:rehearsals`} kind="rehearsals" /></>;
};
export const AvailabilityWorkspace = () => {
  const { band, api, workspaceKey } = useWorkspace();
  return <AvailabilityCalendar key={workspaceKey} band={band} api={api} workspaceKey={workspaceKey} />;
};




const ContentWorkspace = ({ kind }) => {
  const { band, api, workspaceKey } = useWorkspace();
  const { loading, error, entries, pagination, refresh, loadMore } = useCollection(kind);
  const [searchParams, setSearchParams] = useSearchParams();
  const [selected, setSelected] = useState(null); const [formMode, setFormMode] = useState(() => kind === 'rehearsals' && searchParams.get('create') === '1' && band.membership ? 'create' : null);
  const detailParam = kind === 'songs' ? 'song' : 'rehearsal';
  const detailValue = searchParams.get(detailParam);
  const detailId = detailValue == null ? null : strictPositiveId(detailValue);
  const [queryDetail, setQueryDetail] = useState({ id: null, loading: false, error: '', item: null });
  const detailEpoch = useRef(0);
  const member = Boolean(band.membership);
  const label = kind === 'songs' ? '곡' : '합주';
  useEffect(() => {
    if (detailValue == null) {
      detailEpoch.current += 1;
      setQueryDetail({ id: null, loading: false, error: '', item: null });
      return undefined;
    }
    const epoch = ++detailEpoch.current;
    if (!detailId) {
      setQueryDetail({ id: null, loading: false, error: '요청한 항목을 찾을 수 없습니다.', item: null });
      return undefined;
    }
    const controller = new AbortController();
    setQueryDetail({ id: detailId, loading: true, error: '', item: null });
    const resource = kind === 'songs' ? 'song' : 'rehearsal';
    api.requestJson(bandWorkspacePaths[resource](band.id, detailId), requestOptions(controller.signal)).then((response) => {
      const item = response?.[resource];
      const sameBand = item?.band_id == null || Number(item.band_id) === Number(band.id);
      if (controller.signal.aborted || epoch !== detailEpoch.current) return;
      if (!item || Number(item.id) !== detailId || !sameBand) {
        setQueryDetail({ id: detailId, loading: false, error: '요청한 항목을 찾을 수 없습니다.', item: null });
        return;
      }
      setQueryDetail({ id: detailId, loading: false, error: '', item });
    }).catch(() => {
      if (!controller.signal.aborted && epoch === detailEpoch.current) setQueryDetail({ id: detailId, loading: false, error: '요청한 항목을 찾을 수 없습니다.', item: null });
    });
    return () => controller.abort();
  }, [api, band.id, detailId, detailValue, kind, workspaceKey]);
  const activeItem = detailValue == null ? selected : (queryDetail.id === detailId && !queryDetail.loading ? queryDetail.item : null);
  const clearDetailQuery = () => {
    const next = new URLSearchParams(searchParams);
    next.delete(detailParam);
    ['create', 'date', 'period', 'availability'].forEach((key) => next.delete(key));
    setSearchParams(next, { replace: true });
  };
  const selectEntry = (entry) => { setSelected(entry); setFormMode(null); if (detailValue != null) clearDetailQuery(); };
  const startCreate = () => { setSelected(null); setFormMode('create'); if (detailValue != null) clearDetailQuery(); };
  const deleteActive = () => { setSelected(null); setFormMode(null); if (detailValue != null) clearDetailQuery(); refresh(); };
  const finishEdit = () => { setFormMode(null); setSelected(null); if (detailValue != null || searchParams.has('create')) clearDetailQuery(); refresh(); };
  const cancelEdit = () => { setFormMode(null); if (searchParams.has('create')) { clearDetailQuery(); refresh(); } };
  return <section className="workspace-page content-workspace" aria-labelledby={`${kind}-title`}>
    <div className="workspace-page-heading"><div><h1 id={`${kind}-title`}>{label} 목록</h1><p>{member ? `${label}을 관리하고 재생 미디어를 확인하세요.` : `공개된 ${label}만 표시합니다.`}</p></div>{member && <button className="primary-btn" onClick={startCreate}>{label} 추가</button>}</div>
    {!member && <WriteGuidance />}
    {loading && entries.length === 0 ? <p aria-live="polite">{label}을 불러오는 중입니다…</p> : error ? <ErrorState message={error} retry={refresh} /> : entries.length === 0 && !formMode && detailValue == null ? <div className="workspace-empty"><p>표시할 {label}이 없습니다.</p>{member && <button className="secondary-btn" onClick={startCreate}>첫 {label} 추가</button>}</div> : <div className="workspace-grid"><div className="workspace-list"><ul>{entries.map((entry) => <li key={entry.id}><button className={activeItem?.id === entry.id ? 'workspace-row selected' : 'workspace-row'} onClick={() => selectEntry(entry)}><strong>{entry.title}</strong><span>{kind === 'songs' ? entry.artist : entry.date}</span>{kind === 'rehearsals' && <RehearsalTypeBadge type={entry.rehearsal_type} />}<small>{entry.effective_visibility === 'MEMBERS' ? '멤버 전용' : '공개'} · {entry.status || (entry.songs?.length ? `${entry.songs.length}곡` : '')}</small></button></li>)}</ul>{pagination?.has_more && <button className="secondary-btn" disabled={loading} onClick={loadMore}>{loading ? '불러오는 중…' : '더 불러오기'}</button>}</div>
      <div className="workspace-detail">{formMode ? <ContentForm key={`${kind}:${formMode}:${formMode === 'edit' ? activeItem?.id ?? '' : 'new'}`} kind={kind} item={formMode === 'edit' ? activeItem : null} onDone={finishEdit} onCancel={cancelEdit} /> : detailValue != null ? queryDetail.loading ? <p aria-live="polite">{label} 정보를 불러오는 중입니다…</p> : queryDetail.error ? <ErrorState message={queryDetail.error} /> : activeItem ? <ContentDetail kind={kind} item={activeItem} onEdit={() => setFormMode('edit')} onDeleted={deleteActive} /> : <p className="workspace-placeholder">항목을 불러오지 못했습니다.</p> : activeItem ? <ContentDetail kind={kind} item={activeItem} onEdit={() => setFormMode('edit')} onDeleted={deleteActive} /> : <p className="workspace-placeholder">항목을 선택하면 상세 정보와 재생 미디어를 볼 수 있습니다.</p>}</div></div>}
  </section>;
};

const ContentDetail = ({ kind, item, onEdit, onDeleted }) => {
  const { band, api, workspaceKey } = useWorkspace(); const [mediaState, setMediaState] = useState({ loading: true, error: '', pages: [], pagination: null });
  const requestGate = useRef(createWorkspaceRequestGate()); const controllers = useRef(new Set()); const staleAttempts = useRef(new Set());
  const mediaPath = useCallback((offset) => (kind === 'songs' ? bandWorkspacePaths.songMedia(band.id, item.id, { limit: PAGE_SIZE, offset }) : bandWorkspacePaths.rehearsalMedia(band.id, item.id, { limit: PAGE_SIZE, offset })), [band.id, item.id, kind]);
  const loadMedia = useCallback(async (offset = 0, replace = false) => {
    const controller = new AbortController(); controllers.current.add(controller); const token = requestGate.current.begin(`${workspaceKey}:${item.id}:${offset}`);
    setMediaState((previous) => ({ ...previous, loading: true, error: '' }));
    try { const response = await api.requestJson(mediaPath(offset), requestOptions(controller.signal));
      if (controller.signal.aborted || !requestGate.current.accepts(`${workspaceKey}:${item.id}:${offset}`, token)) return;
      const page = { offset, media: Array.isArray(response?.media) ? response.media : [] };
      setMediaState((previous) => ({ loading: false, error: '', pages: replace ? [page] : mergeMediaPage(previous.pages, page), pagination: response?.pagination ?? previous.pagination }));
    } catch { if (!controller.signal.aborted && requestGate.current.accepts(`${workspaceKey}:${item.id}:${offset}`, token)) setMediaState((previous) => ({ ...previous, loading: false, error: '미디어를 불러오지 못했습니다.' }));
    } finally { controllers.current.delete(controller); }
  }, [api, item.id, mediaPath, workspaceKey]);
  useEffect(() => { const activeControllers = controllers.current; loadMedia(0); return () => { activeControllers.forEach((controller) => controller.abort()); activeControllers.clear(); }; }, [loadMedia]);
  const refreshAuthoritativeMedia = useCallback(() => loadMedia(0, true), [loadMedia]);
  const updateMedia = useCallback((updated) => {
    if (!updated?.id) return;
    setMediaState((previous) => ({ ...previous, pages: previous.pages.map((page) => ({ ...page, media: page.media.map((media) => media.id === updated.id ? { ...media, ...updated } : media) })) }));
  }, []);
  const resourceLabel = kind === 'songs' ? '곡' : '합주';
  return <article><div className="detail-heading"><div><h2>{item.title}</h2><p>{kind === 'songs' ? item.artist : `${item.date}${item.time ? ` · ${item.time}` : ''}`}</p></div>{item.can_edit && <button className="secondary-btn" onClick={onEdit}>수정</button>}<BandDeleteAction band={band} workspaceKey={workspaceKey} canDelete={item.can_delete} label={resourceLabel} path={kind === 'songs' ? bandWorkspacePaths.song(band.id, item.id) : bandWorkspacePaths.rehearsal(band.id, item.id)} onDeleted={onDeleted} /></div>
    {kind === 'songs' ? <dl><dt>상태</dt><dd>{item.status || '-'}</dd><dt>장르</dt><dd>{item.genre || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl> : <dl><dt>합주 유형</dt><dd><RehearsalTypeBadge type={item.rehearsal_type} /></dd><dt>장소</dt><dd>{item.location || '-'}</dd><dt>연결된 곡</dt><dd>{item.songs?.map((song) => song.title).join(', ') || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl>}
    {kind === 'songs' && band.membership && <BandUploadInput band={band} songId={item.id} parentVisibility={item.effective_visibility || item.visibility} onMediaComplete={refreshAuthoritativeMedia} />}
    <h3>재생 미디어</h3>{mediaState.loading && mediaState.pages.length === 0 ? <p>미디어를 불러오는 중입니다…</p> : mediaState.error && mediaState.pages.length === 0 ? <p className="workspace-muted">{mediaState.error}</p> : mediaState.pages.length === 0 ? <p className="workspace-muted">표시할 미디어가 없습니다. 업로드는 준비 중입니다.</p> : <><div className="media-list">{mediaState.pages.flatMap((page) => page.media.map((media) => <PlaybackMedia key={media.id} media={media} band={band} api={api} workspaceKey={workspaceKey} onMediaUpdate={updateMedia} onAuthoritativeRefresh={refreshAuthoritativeMedia} onDeleted={refreshAuthoritativeMedia} onStale={() => { if (takeOneStaleRefresh(staleAttempts.current, page.offset, media.id)) loadMedia(page.offset); }} />))}</div>{mediaState.pagination?.has_more && <button className="secondary-btn" disabled={mediaState.loading} onClick={() => loadMedia(mediaState.pagination.next_offset)}>{mediaState.loading ? '미디어를 불러오는 중…' : '미디어 더 불러오기'}</button>}{mediaState.error && <p className="workspace-muted" role="alert">{mediaState.error}</p>}</>}
  </article>;
};


const ContentForm = ({ kind, item, onDone, onCancel }) => {
  const { config } = useServicePolicy();
  const { band, api, workspaceKey } = useWorkspace(); const [saving, setSaving] = useState(false); const savingRef = useRef(false); const [error, setError] = useState(''); const [songs, setSongs] = useState([]);
  const [resultUnknown, setResultUnknown] = useState(false);
  const [searchParams] = useSearchParams();
  const candidate = useRef(!item && kind === 'rehearsals' && validDay(searchParams.get('date')) && Object.hasOwn(PERIOD_LABELS, searchParams.get('period')) ? { date: searchParams.get('date'), period: searchParams.get('period'), fingerprint: searchParams.get('availability') || '' } : null);
  const saveController = useRef(null); const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; saveController.current?.abort(); }; }, []);
  const originalRehearsalType = useRef(kind === 'rehearsals' && item ? normalizeRehearsalType(item.rehearsal_type) : 'REGULAR');
  const initial = kind === 'songs' ? { title: item?.title || '', artist: item?.artist || '', status: item?.status || 'Practice', chords: item?.chords || '', link: item?.link || '', memo: item?.memo || '', genre: item?.genre || '', difficulty: item?.difficulty || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS' } : { title: item?.title || '', date: item?.date || candidate.current?.date || '', start_date: item?.start_date || '', end_date: item?.end_date || '', time: item?.time || '', location: item?.location || '', memo: item?.memo || '', color: item?.color || '', rehearsal_type: item ? normalizeRehearsalType(item.rehearsal_type) : 'REGULAR', visibility: item?.visibility || band.default_visibility || 'MEMBERS', song_ids: item?.songs?.map((song) => String(song.id)) || [] };
  const [value, setValue] = useState(initial);
  useEffect(() => { if (kind !== 'rehearsals') return undefined; const controller = new AbortController(); api.requestJson(bandWorkspacePaths.songs(band.id, { limit: 100, offset: 0 }), requestOptions(controller.signal)).then((response) => setSongs(response?.songs || [])).catch(() => setSongs([])); return () => controller.abort(); }, [api, band.id, kind]);
  const change = (event) => setValue((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  const save = async (event) => {
    event.preventDefault(); if (savingRef.current || resultUnknown) return;
    const relationshipsChanged = kind === 'rehearsals' && Boolean(item) && [...(item.songs || []).map((song) => String(song.id))].sort().join(',') !== [...value.song_ids].sort().join(',');
    const decision = reviewPublicVisibility(config, { previous: item?.visibility, next: value.visibility, relationshipsChanged });
    if (!decision.proceed) { if (decision.error) setError(decision.error); return; }
    savingRef.current = true; setSaving(true); setError(''); const controller = new AbortController(); saveController.current = controller; let submitted = false;
    try {
      if (candidate.current) {
        const current = await api.requestJson(availabilityPath(band.id, value.date, value.date), { signal: controller.signal });
        if (controller.signal.aborted || !live.current) return;
        const fingerprint = availabilityFingerprint(current, value.date);
        if (value.date !== candidate.current.date || fingerprint !== candidate.current.fingerprint) {
          const changed = current.days.filter((day) => day.date === value.date); const available = changed.filter((day) => day[candidate.current.period] === 'AVAILABLE').length;
          if (!window.confirm(`가능 일정이나 날짜가 변경되었습니다. ${value.date} ${PERIOD_LABELS[candidate.current.period]} 현재 가능 ${available}명 / 활성 멤버 ${current.members.length}명입니다. 미응답은 가능에 포함하지 않습니다. 이대로 합주를 저장할까요?`)) return;
        }
      }
      const path = kind === 'songs' ? (item ? bandWorkspacePaths.song(band.id, item.id) : bandWorkspacePaths.songs(band.id)) : (item ? bandWorkspacePaths.rehearsal(band.id, item.id) : bandWorkspacePaths.rehearsals(band.id));
      submitted = true;
      const rehearsalValue = kind === 'rehearsals' && item && value.rehearsal_type === originalRehearsalType.current ? { ...value, rehearsal_type: undefined } : value;
      await api.requestJson(path, { method: item ? 'PATCH' : 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...(kind === 'songs' ? songPayload(value) : rehearsalPayload(rehearsalValue)), ...(decision.confirmation ? { visibility_confirm: decision.confirmation } : {}) }) });
      if (!controller.signal.aborted && live.current) onDone();
    } catch (cause) { if (!controller.signal.aborted && live.current) {
      if (candidate.current && submitted && (!cause.status || cause.status >= 500)) { setResultUnknown(true); setError('저장 결과를 확인하지 못했습니다. 중복 생성을 막기 위해 다시 저장하지 않습니다. 취소로 폼을 닫은 뒤 합주 목록을 새로고침해 결과를 확인해 주세요.'); }
      else setError('저장하지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.');
    } }
    finally { savingRef.current = false; if (live.current) setSaving(false); }
  };
  return <form className="workspace-form" onSubmit={save}><h2>{item ? '수정' : '새로 추가'}</h2>{candidate.current && <p className="availability-notice">조율 후보: {candidate.current.date} · {PERIOD_LABELS[candidate.current.period]}. 정확한 합주 시각은 직접 입력해 주세요. 저장 직전 가능 상태를 다시 확인하지만 예약이나 참석을 보장하지는 않습니다.</p>}<label>제목<input required name="title" value={value.title} onChange={change} /></label>{kind === 'songs' ? <><label>아티스트<input required name="artist" value={value.artist} onChange={change} /></label><label>상태<select name="status" value={value.status} onChange={change}><option>Practice</option><option>Completed</option><option>OnHold</option></select></label><label>장르<input name="genre" value={value.genre} onChange={change} /></label><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label></> : <><label>합주 유형<select name="rehearsal_type" value={value.rehearsal_type} onChange={change}>{REHEARSAL_TYPES.map(([type, text]) => <option key={type} value={type}>{type === 'UNCLASSIFIED' ? `${text} (유형 선택)` : text}</option>)}</select></label>{value.rehearsal_type === 'UNCLASSIFIED' && <p className="workspace-muted">기존 합주 유형은 추정하지 않았습니다. 실제 유형을 선택해 주세요.</p>}<label>날짜<input required type="date" name="date" value={value.date} onChange={change} /></label><label>시간<input name="time" value={value.time} onChange={change} /></label><label>장소<input name="location" value={value.location} maxLength={200} onChange={change} /></label><BandPlacePicker bandId={band.id} workspaceKey={workspaceKey} api={api} onSelect={(location) => setValue((previous) => ({ ...previous, location }))} /><fieldset><legend>연결할 곡</legend>{songs.map((song) => <label className="check-row" key={song.id}><input type="checkbox" checked={value.song_ids.includes(String(song.id))} onChange={(event) => setValue((previous) => ({ ...previous, song_ids: event.target.checked ? [...previous.song_ids, String(song.id)] : previous.song_ids.filter((id) => id !== String(song.id)) }))} />{song.title} — {song.artist}</label>)}</fieldset><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label></>}<label>공개 범위<select name="visibility" value={value.visibility} onChange={change}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={onCancel}>취소</button><button className="primary-btn" disabled={saving || resultUnknown}>{saving ? '저장 중…' : '저장'}</button></div></form>;
};
