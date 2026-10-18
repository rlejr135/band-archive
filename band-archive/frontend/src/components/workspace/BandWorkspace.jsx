import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';
import BandSwitcher from '../bands/BandSwitcher.jsx';
import BandActivityDashboard from '../dashboard/BandActivityDashboard.jsx';
import AvailabilityCalendar, { RehearsalTabs } from './AvailabilityCalendar.jsx';
import { availabilityFingerprint, availabilityPath, PERIOD_LABELS, validDay } from './availabilityState.js';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandWorkspacePaths, rehearsalPayload, songPayload, suggestionPayload, votePayload } from '../../services/bandWorkspaceApi.js';
import { uploadBandGalleryImageFile } from '../../services/mediaUploadManager.js';
import { safeExternalHttpUrl } from '../../services/safeExternalUrl.js';
import { MediaEngagement } from './MediaEngagement.jsx';
import BandPlacePicker from './BandPlacePicker.jsx';
import { MemberProfileEditor, MemberProfileSummary } from './MemberProfile.jsx';
import { BandDeleteAction } from './BandDeleteAction.jsx';
import UploadCenter from '../common/UploadCenter.jsx';
import FileUpload from '../common/FileUpload.jsx';
import { UploadContext } from '../../context/UploadContext.jsx';
import { createWorkspaceRequestGate, mergeMediaPage, takeOneStaleRefresh, WorkspacePaintGate, workspaceIdentityKey } from './workspaceRouteState.js';
import { strictPositiveId } from './bandDashboardState.js';
import './BandWorkspace.css';

// This file intentionally starts request state from effects: each state reset
// prevents a previous band/selection from rendering while its replacement is
// being resolved. The guarded async callbacks then own the terminal state.
/* eslint-disable react-hooks/set-state-in-effect */

const WorkspaceContext = createContext(null);
const PAGE_SIZE = 20;

const useWorkspace = () => {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error('useWorkspace must be used in BandWorkspace');
  return value;
};

// The application shell always supplies this context. Keeping this boundary
// inert without it also lets read-only embedded/SSR workspace surfaces remain
// genuinely read-only instead of creating an unscoped upload task.
const BandUploadInput = (props) => useContext(UploadContext) ? <FileUpload {...props} /> : null;

const requestOptions = (signal) => ({ authenticated: false, optionalAuth: true, signal });
const genericError = '정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.';

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
  const { user, getAccessToken, refreshSession, signOut } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [state, setState] = useState({ loading: true, error: '', band: null });
  const requestGate = useRef(createWorkspaceRequestGate());
  const currentBandRequest = useRef({ routeKey: '', version: null });
  const logoRefreshes = useRef(new Set());
  const logoRefreshController = useRef(null);
  const [failedLogoKey, setFailedLogoKey] = useState('');
  const routeKey = workspaceIdentityKey(bandSlug, user?.id);

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
          <BandSwitcher currentSlug={state.band.slug} />
          <Link className="nav-btn workspace-account-link" to="/bands">내 계정</Link>
          {user ? <button className="nav-btn" onClick={async () => { await signOut(); navigate('/', { replace: true }); }}>로그아웃</button> : <button className="nav-btn" onClick={() => navigate('/login')}>로그인</button>}
        </div>
        <nav aria-label="밴드 공간">
          <NavLink end to={`/bands/${encodeURIComponent(state.band.slug)}`}>대시보드</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/songs`}>곡</NavLink>
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

const useCollection = (kind) => {
  const { band, api, workspaceKey } = useWorkspace();
  const [state, setState] = useState({ loading: true, error: '', entries: [], pagination: null });
  const version = useRef(0);
  const resource = kind === 'songs' ? 'songs' : 'rehearsals';
  const load = useCallback(async ({ append = false, offset = 0 } = {}) => {
    const controller = new AbortController();
    const token = ++version.current;
    setState((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const response = await api.requestJson(bandWorkspacePaths[resource](band.id, { limit: PAGE_SIZE, offset }), requestOptions(controller.signal));
      if (token !== version.current) return;
      const next = Array.isArray(response?.[resource]) ? response[resource] : [];
      setState((previous) => ({ loading: false, error: '', entries: append ? [...previous.entries, ...next] : next, pagination: response?.pagination ?? null }));
    } catch {
      if (token === version.current) setState((previous) => ({ ...previous, loading: false, error: genericError }));
    }
  }, [api, band.id, resource]);
  useEffect(() => { version.current += 1; load(); }, [load, workspaceKey]);
  const refresh = useCallback(() => load(), [load]);
  const loadMore = useCallback(() => load({ append: true, offset: state.pagination?.next_offset ?? state.entries.length }), [load, state.entries.length, state.pagination?.next_offset]);
  return { ...state, refresh, loadMore };
};

export const BandDashboard = () => {
  const { band, api, workspaceKey } = useWorkspace();
  return <BandActivityDashboard key={workspaceKey} band={band} api={api} workspaceKey={workspaceKey} />;
};

const ErrorState = ({ message, retry }) => <div className="workspace-error" role="alert"><p>{message}</p>{retry && <button className="secondary-btn" onClick={retry}>다시 시도</button>}</div>;
const WriteGuidance = () => {
  const { band } = useWorkspace(); const { user } = useAuth(); const navigate = useNavigate();
  if (band.membership) return null;
  return <aside className="workspace-guidance">{user ? '이 밴드의 멤버가 되면 곡과 합주를 작성할 수 있습니다.' : <><span>작성하려면 로그인 후 밴드에 가입해 주세요.</span><button className="secondary-btn" onClick={() => navigate('/login')}>로그인</button></>}</aside>;
};

export const SongWorkspace = () => {
  const { band, workspaceKey } = useWorkspace();
  return <ContentWorkspace key={`${workspaceKey}:${band.id}:songs`} kind="songs" />;
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
    {loading && entries.length === 0 ? <p aria-live="polite">{label}을 불러오는 중입니다…</p> : error ? <ErrorState message={error} retry={refresh} /> : entries.length === 0 && !formMode && detailValue == null ? <div className="workspace-empty"><p>표시할 {label}이 없습니다.</p>{member && <button className="secondary-btn" onClick={startCreate}>첫 {label} 추가</button>}</div> : <div className="workspace-grid"><div className="workspace-list"><ul>{entries.map((entry) => <li key={entry.id}><button className={activeItem?.id === entry.id ? 'workspace-row selected' : 'workspace-row'} onClick={() => selectEntry(entry)}><strong>{entry.title}</strong><span>{kind === 'songs' ? entry.artist : entry.date}</span><small>{entry.effective_visibility === 'MEMBERS' ? '멤버 전용' : '공개'} · {entry.status || (entry.songs?.length ? `${entry.songs.length}곡` : '')}</small></button></li>)}</ul>{pagination?.has_more && <button className="secondary-btn" disabled={loading} onClick={loadMore}>{loading ? '불러오는 중…' : '더 불러오기'}</button>}</div>
      <div className="workspace-detail">{formMode ? <ContentForm kind={kind} item={formMode === 'edit' ? activeItem : null} onDone={finishEdit} onCancel={cancelEdit} /> : detailValue != null ? queryDetail.loading ? <p aria-live="polite">{label} 정보를 불러오는 중입니다…</p> : queryDetail.error ? <ErrorState message={queryDetail.error} /> : activeItem ? <ContentDetail kind={kind} item={activeItem} onEdit={() => setFormMode('edit')} onDeleted={deleteActive} /> : <p className="workspace-placeholder">항목을 불러오지 못했습니다.</p> : activeItem ? <ContentDetail kind={kind} item={activeItem} onEdit={() => setFormMode('edit')} onDeleted={deleteActive} /> : <p className="workspace-placeholder">항목을 선택하면 상세 정보와 재생 미디어를 볼 수 있습니다.</p>}</div></div>}
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
    {kind === 'songs' ? <dl><dt>상태</dt><dd>{item.status || '-'}</dd><dt>장르</dt><dd>{item.genre || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl> : <dl><dt>장소</dt><dd>{item.location || '-'}</dd><dt>연결된 곡</dt><dd>{item.songs?.map((song) => song.title).join(', ') || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl>}
    {kind === 'songs' && band.membership && <BandUploadInput band={band} songId={item.id} onMediaComplete={refreshAuthoritativeMedia} />}
    <h3>재생 미디어</h3>{mediaState.loading && mediaState.pages.length === 0 ? <p>미디어를 불러오는 중입니다…</p> : mediaState.error && mediaState.pages.length === 0 ? <p className="workspace-muted">{mediaState.error}</p> : mediaState.pages.length === 0 ? <p className="workspace-muted">표시할 미디어가 없습니다. 업로드는 준비 중입니다.</p> : <><div className="media-list">{mediaState.pages.flatMap((page) => page.media.map((media) => <PlaybackMedia key={media.id} media={media} band={band} api={api} workspaceKey={workspaceKey} onMediaUpdate={updateMedia} onAuthoritativeRefresh={refreshAuthoritativeMedia} onDeleted={refreshAuthoritativeMedia} onStale={() => { if (takeOneStaleRefresh(staleAttempts.current, page.offset, media.id)) loadMedia(page.offset); }} />))}</div>{mediaState.pagination?.has_more && <button className="secondary-btn" disabled={mediaState.loading} onClick={() => loadMedia(mediaState.pagination.next_offset)}>{mediaState.loading ? '미디어를 불러오는 중…' : '미디어 더 불러오기'}</button>}{mediaState.error && <p className="workspace-muted" role="alert">{mediaState.error}</p>}</>}
  </article>;
};

const PlaybackMedia = ({ media, band, api, workspaceKey, onMediaUpdate, onAuthoritativeRefresh, onDeleted, onStale }) => {
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

const ContentForm = ({ kind, item, onDone, onCancel }) => {
  const { band, api, workspaceKey } = useWorkspace(); const [saving, setSaving] = useState(false); const savingRef = useRef(false); const [error, setError] = useState(''); const [songs, setSongs] = useState([]);
  const [resultUnknown, setResultUnknown] = useState(false);
  const [searchParams] = useSearchParams();
  const candidate = useRef(!item && kind === 'rehearsals' && validDay(searchParams.get('date')) && Object.hasOwn(PERIOD_LABELS, searchParams.get('period')) ? { date: searchParams.get('date'), period: searchParams.get('period'), fingerprint: searchParams.get('availability') || '' } : null);
  const saveController = useRef(null); const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; saveController.current?.abort(); }; }, []);
  const initial = kind === 'songs' ? { title: item?.title || '', artist: item?.artist || '', status: item?.status || 'Practice', chords: item?.chords || '', link: item?.link || '', memo: item?.memo || '', genre: item?.genre || '', difficulty: item?.difficulty || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS' } : { title: item?.title || '', date: item?.date || candidate.current?.date || '', start_date: item?.start_date || '', end_date: item?.end_date || '', time: item?.time || '', location: item?.location || '', memo: item?.memo || '', color: item?.color || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS', song_ids: item?.songs?.map((song) => String(song.id)) || [] };
  const [value, setValue] = useState(initial);
  useEffect(() => { if (kind !== 'rehearsals') return undefined; const controller = new AbortController(); api.requestJson(bandWorkspacePaths.songs(band.id, { limit: 100, offset: 0 }), requestOptions(controller.signal)).then((response) => setSongs(response?.songs || [])).catch(() => setSongs([])); return () => controller.abort(); }, [api, band.id, kind]);
  const change = (event) => setValue((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  const save = async (event) => {
    event.preventDefault(); if (savingRef.current || resultUnknown) return; savingRef.current = true; setSaving(true); setError(''); const controller = new AbortController(); saveController.current = controller; let submitted = false;
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
      await api.requestJson(path, { method: item ? 'PATCH' : 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(kind === 'songs' ? songPayload(value) : rehearsalPayload(value)) });
      if (!controller.signal.aborted && live.current) onDone();
    } catch (cause) { if (!controller.signal.aborted && live.current) {
      if (candidate.current && submitted && (!cause.status || cause.status >= 500)) { setResultUnknown(true); setError('저장 결과를 확인하지 못했습니다. 중복 생성을 막기 위해 다시 저장하지 않습니다. 취소로 폼을 닫은 뒤 합주 목록을 새로고침해 결과를 확인해 주세요.'); }
      else setError('저장하지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.');
    } }
    finally { savingRef.current = false; if (live.current) setSaving(false); }
  };
  return <form className="workspace-form" onSubmit={save}><h2>{item ? '수정' : '새로 추가'}</h2>{candidate.current && <p className="availability-notice">조율 후보: {candidate.current.date} · {PERIOD_LABELS[candidate.current.period]}. 정확한 합주 시각은 직접 입력해 주세요. 저장 직전 가능 상태를 다시 확인하지만 예약이나 참석을 보장하지는 않습니다.</p>}<label>제목<input required name="title" value={value.title} onChange={change} /></label>{kind === 'songs' ? <><label>아티스트<input required name="artist" value={value.artist} onChange={change} /></label><label>상태<select name="status" value={value.status} onChange={change}><option>Practice</option><option>Completed</option><option>OnHold</option></select></label><label>장르<input name="genre" value={value.genre} onChange={change} /></label><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label></> : <><label>날짜<input required type="date" name="date" value={value.date} onChange={change} /></label><label>시간<input name="time" value={value.time} onChange={change} /></label><label>장소<input name="location" value={value.location} maxLength={200} onChange={change} /></label><BandPlacePicker bandId={band.id} workspaceKey={workspaceKey} api={api} onSelect={(location) => setValue((previous) => ({ ...previous, location }))} /><fieldset><legend>연결할 곡</legend>{songs.map((song) => <label className="check-row" key={song.id}><input type="checkbox" checked={value.song_ids.includes(String(song.id))} onChange={(event) => setValue((previous) => ({ ...previous, song_ids: event.target.checked ? [...previous.song_ids, String(song.id)] : previous.song_ids.filter((id) => id !== String(song.id)) }))} />{song.title} — {song.artist}</label>)}</fieldset><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label></>}<label>공개 범위<select name="visibility" value={value.visibility} onChange={change}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={onCancel}>취소</button><button className="primary-btn" disabled={saving || resultUnknown}>{saving ? '저장 중…' : '저장'}</button></div></form>;
};

export const AnnouncementEditor = ({ announcement, band, api, workspaceKey, onSaved, onForbidden }) => {
  const [editing, setEditing] = useState(false); const [content, setContent] = useState(''); const [visibility, setVisibility] = useState('MEMBERS'); const [saving, setSaving] = useState(false); const [error, setError] = useState('');
  const savingRef = useRef(false); const generation = useRef(0); const controllerRef = useRef(null);
  const defaultVisibility = announcement?.visibility === 'PUBLIC' || announcement?.visibility === 'MEMBERS' ? announcement.visibility : band.default_visibility === 'PUBLIC' ? 'PUBLIC' : 'MEMBERS';
  useEffect(() => {
    generation.current += 1;
    controllerRef.current?.abort(); controllerRef.current = null;
    savingRef.current = false;
    setEditing(false); setContent(announcement?.content || ''); setVisibility(defaultVisibility); setSaving(false); setError('');
    return () => { generation.current += 1; controllerRef.current?.abort(); controllerRef.current = null; savingRef.current = false; };
  }, [announcement?.content, announcement?.visibility, defaultVisibility, workspaceKey]);
  const save = async (event) => {
    event.preventDefault(); if (savingRef.current || !content.trim()) return;
    const token = ++generation.current; const controller = new AbortController();
    controllerRef.current?.abort(); controllerRef.current = controller;
    savingRef.current = true; setSaving(true); setError('');
    try {
      const response = await api.requestJson(bandWorkspacePaths.announcement(band.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: content.trim(), visibility }), signal: controller.signal });
      if (controller.signal.aborted || token !== generation.current) return;
      if (response?.announcement) onSaved?.(response.announcement);
      setEditing(false);
    } catch (saveError) {
      if (controller.signal.aborted || token !== generation.current) return;
      if (saveError?.status === 403) onForbidden?.();
      else setError('공지사항을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      if (!controller.signal.aborted && token === generation.current) { savingRef.current = false; controllerRef.current = null; setSaving(false); }
    }
  };
  const canManage = announcement ? announcement.can_manage : band.can_manage;
  return <section className="workspace-announcement" aria-label="공지사항"><div className="detail-heading"><h2>공지사항</h2>{canManage && !editing && <button type="button" className="secondary-btn" onClick={() => setEditing(true)}>{announcement ? '공지 수정' : '공지 작성'}</button>}<BandDeleteAction band={band} workspaceKey={`${workspaceKey}:announcement`} canDelete={Boolean(announcement?.content) && announcement?.can_delete} label="공지사항" path={bandWorkspacePaths.announcement(band.id)} onDeleted={() => onSaved?.({ ...announcement, content: '', visibility: defaultVisibility })} /></div>
    {editing ? <form className="workspace-form" onSubmit={save}><label>공지 내용<textarea aria-label="공지 내용" maxLength={10000} value={content} onChange={(event) => setContent(event.target.value)} /></label><label>공개 범위<select aria-label="공지 공개 범위" value={visibility} onChange={(event) => setVisibility(event.target.value)}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={() => { setEditing(false); setContent(announcement?.content || ''); setVisibility(defaultVisibility); }}>취소</button><button className="primary-btn" disabled={saving || !content.trim()}>{saving ? '저장 중…' : '저장'}</button></div></form> : announcement?.content ? <p>{announcement.content}</p> : <p className="workspace-muted">현재 공개된 공지사항이 없습니다.</p>}
  </section>;
};

const dedupeEntries = (entries) => {
  const seen = new Set();
  return entries.filter((entry) => {
    if (seen.has(entry.id)) return false;
    seen.add(entry.id);
    return true;
  });
};

// The route shell keys every workspace by slug and account. This hook adds an
// abort plus a generation fence for pagination, so a late page cannot paint
// after either a resource selection or the workspace itself has changed.
const usePagedRead = (resourceKey, fetchPage) => {
  const { workspaceKey } = useWorkspace();
  const [state, setState] = useState({ loading: true, error: '', notFound: false, entries: [], pagination: null });
  const gate = useRef(createWorkspaceRequestGate());
  const controllers = useRef(new Set());
  const readKey = `${workspaceKey}:${resourceKey}`;
  const load = useCallback(async ({ offset = 0, append = false } = {}) => {
    const controller = new AbortController();
    controllers.current.add(controller);
    const token = gate.current.begin(`${readKey}:${offset}`);
    setState((previous) => ({ ...previous, loading: true, error: '', notFound: false }));
    try {
      const response = await fetchPage({ limit: PAGE_SIZE, offset }, controller.signal);
      if (controller.signal.aborted || !gate.current.accepts(`${readKey}:${offset}`, token)) return;
      const next = Array.isArray(response?.entries) ? response.entries : [];
      setState((previous) => ({ loading: false, error: '', notFound: false, entries: dedupeEntries(append ? [...previous.entries, ...next] : next), pagination: response?.pagination ?? null }));
    } catch (error) {
      if (!controller.signal.aborted && gate.current.accepts(`${readKey}:${offset}`, token)) {
        setState((previous) => ({ ...previous, loading: false, error: genericError, notFound: error?.status === 404 }));
      }
    } finally { controllers.current.delete(controller); }
  }, [fetchPage, readKey]);
  useEffect(() => {
    const activeControllers = controllers.current;
    setState({ loading: true, error: '', notFound: false, entries: [], pagination: null });
    load();
    return () => { activeControllers.forEach((controller) => controller.abort()); activeControllers.clear(); };
  }, [load, readKey]);
  return {
    ...state,
    refresh: useCallback(() => load(), [load]),
    loadMore: useCallback(() => load({ offset: state.pagination?.next_offset ?? state.entries.length, append: true }), [load, state.entries.length, state.pagination?.next_offset]),
  };
};

const RosterUnavailable = () => <section className="workspace-page workspace-empty" role="status"><h1>멤버</h1><p>요청한 밴드 정보는 현재 표시할 수 없습니다.</p></section>;

export const MembersWorkspace = () => {
  const { band, api, workspaceKey } = useWorkspace();
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const activeMember = band.membership && String(band.membership.status || 'ACTIVE').toLowerCase() === 'active';
  const fetchMembers = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.members(band.id, page), requestOptions(signal));
    return { entries: response?.members, pagination: response?.pagination };
  }, [api, band.id]);
  const { loading, error, notFound, entries, pagination, refresh, loadMore } = usePagedRead('members', fetchMembers);
  const rosterUnavailable = !loading && notFound;
  if (rosterUnavailable) return <RosterUnavailable />;
  const startEditing = () => { setSaved(false); setEditing(true); };
  return <section className="workspace-page" aria-labelledby="workspace-members-title"><div className="workspace-page-heading"><div><h1 id="workspace-members-title">멤버</h1><p>밴드에서 공개한 멤버와 연습 기록입니다.</p></div>{activeMember && !editing && <button type="button" className="secondary-btn" onClick={startEditing}>내 프로필 수정</button>}</div>
    {saved && <p role="status">내 프로필을 저장했습니다.</p>}
    {editing && <MemberProfileEditor key={workspaceKey} band={band} api={api} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); setSaved(true); refresh(); }} />}
    {loading && entries.length === 0 ? <p aria-live="polite">멤버를 불러오는 중입니다…</p> : error && entries.length === 0 ? <ErrorState message={genericError} retry={refresh} /> : entries.length === 0 ? <div className="workspace-empty"><p>표시할 멤버가 없습니다.</p></div> : <div className="workspace-cards">{entries.map((member) => <article className="workspace-card member-profile-card" key={member.id}><NavLink className="workspace-card-link" to={`/bands/${encodeURIComponent(band.slug)}/members/${member.id}`}><strong>{member.name || '멤버'}{member.is_self && <small className="member-self-badge">나</small>}</strong><MemberProfileSummary member={member} compact /></NavLink>{member.is_self && member.can_edit_profile && !editing && <button type="button" className="secondary-btn" onClick={startEditing}>내 프로필 수정</button>}</article>)}</div>}
    {pagination?.has_more && <button className="secondary-btn" disabled={loading} onClick={loadMore}>{loading ? '멤버를 불러오는 중…' : '멤버 더 불러오기'}</button>}
    {error && entries.length > 0 && <ErrorState message={genericError} retry={refresh} />}
  </section>;
};

export const MemberDetailWorkspace = () => {
  const { memberId } = useParams();
  const { workspaceKey } = useWorkspace();
  return <MemberDetailReadOnly key={`${workspaceKey}:member:${memberId}`} memberId={memberId} />;
};

const MemberDetailReadOnly = ({ memberId }) => {
  const { band, api, workspaceKey } = useWorkspace();
  const navigate = useNavigate();
  const [memberState, setMemberState] = useState({ loading: true, error: '', member: null });
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const memberGate = useRef(createWorkspaceRequestGate());
  useEffect(() => {
    const controller = new AbortController();
    const key = `${workspaceKey}:member:${memberId}`;
    const token = memberGate.current.begin(key);
    setMemberState({ loading: true, error: '', member: null });
    api.requestJson(bandWorkspacePaths.member(band.id, memberId), requestOptions(controller.signal)).then((response) => {
      if (!controller.signal.aborted && memberGate.current.accepts(key, token)) setMemberState({ loading: false, error: '', member: response?.member ?? null });
    }).catch(() => {
      if (!controller.signal.aborted && memberGate.current.accepts(key, token)) setMemberState({ loading: false, error: genericError, member: null });
    });
    return () => controller.abort();
  }, [api, band.id, memberId, workspaceKey]);
  const fetchLogs = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.memberLogs(band.id, memberId, page), requestOptions(signal));
    return { entries: response?.logs, pagination: response?.pagination };
  }, [api, band.id, memberId]);
  const logs = usePagedRead(`member-logs:${memberId}`, fetchLogs);
  const [selectedLogId, setSelectedLogId] = useState(null);
  // Refreshing a log page also renews its short-lived signed URL. Keep only
  // the id in local state so a successful refresh replaces the rendered URL,
  // rather than continuing to play the expired object from a stale snapshot.
  const selectedLog = logs.entries.find((log) => log.id === selectedLogId) ?? null;
  if (!memberState.loading && (memberState.error || !memberState.member)) return <RosterUnavailable />;
  return <section className="workspace-page" aria-labelledby="workspace-member-title">
    {memberState.loading ? <p aria-live="polite">멤버 정보를 불러오는 중입니다…</p> : <><div className="workspace-page-heading"><div><h1 id="workspace-member-title">{memberState.member.name || '멤버'}{memberState.member.is_self && <small className="member-self-badge">나</small>}</h1></div><NavLink className="secondary-btn workspace-button-link" to={`/bands/${encodeURIComponent(band.slug)}/members`}>목록으로</NavLink></div>
      <MemberProfileSummary member={memberState.member} />
      {['inactive', 'left', 'pending', 'rejected'].includes(memberState.member.membership_status) && <p className="workspace-muted" role="status">현재 밴드 멤버가 아닙니다. 이전 연습 기록은 보존됩니다.</p>}
      {memberState.member.is_self && memberState.member.can_edit_profile && !editing && <button type="button" className="secondary-btn" onClick={() => { setSaved(false); setEditing(true); }}>내 프로필 수정</button>}
      {saved && <p role="status">내 프로필을 저장했습니다.</p>}
      {editing && <MemberProfileEditor key={`${workspaceKey}:${memberId}`} band={band} api={api} expectedMemberId={memberId} onCancel={() => setEditing(false)} onSaved={(member) => { setMemberState({ loading: false, error: '', member }); setEditing(false); setSaved(true); }} />}
      <div className="detail-heading"><h2>개인 연습 기록</h2><BandDeleteAction band={band} workspaceKey={`${workspaceKey}:member:${memberId}`} canDelete={memberState.member.can_delete} label="멤버 프로필" path={bandWorkspacePaths.member(band.id, memberId)} onDeleted={() => navigate(`/bands/${encodeURIComponent(band.slug)}/members`, { replace: true })} /></div>
      {logs.loading && logs.entries.length === 0 ? <p aria-live="polite">기록을 불러오는 중입니다…</p> : logs.entries.length === 0 ? <p className="workspace-muted">표시할 개인 연습 기록이 없습니다.</p> : <div className="workspace-grid"><div className="workspace-list"><ul>{logs.entries.map((log) => <li key={log.id}><button className={selectedLog?.id === log.id ? 'workspace-row selected' : 'workspace-row'} onClick={() => setSelectedLogId(log.id)}><strong>{log.title || '개인 연습 기록'}</strong><span>{log.created_at ? new Date(log.created_at).toLocaleString() : ''}</span><small>{log.file_type === 'video' ? '영상' : '오디오'}</small></button></li>)}</ul>{logs.pagination?.has_more && <button className="secondary-btn" disabled={logs.loading} onClick={logs.loadMore}>{logs.loading ? '기록을 불러오는 중…' : '기록 더 불러오기'}</button>}</div><div className="workspace-detail">{selectedLog ? <PersonalLogPlayback log={selectedLog} workspaceKey={`${workspaceKey}:member:${memberId}`} onRefresh={logs.refresh} onDeleted={() => { setSelectedLogId(null); logs.refresh(); }} /> : <p className="workspace-placeholder">기록을 선택하면 재생할 수 있습니다.</p>}</div></div>}
      {logs.error && <ErrorState message={genericError} retry={logs.refresh} />}</>}
  </section>;
};

const PersonalLogPlayback = ({ log, workspaceKey, onRefresh, onDeleted }) => {
  const { band, api } = useWorkspace();
  const [processing, setProcessing] = useState(null);
  const [processingError, setProcessingError] = useState(false);
  const staleAttempt = useRef(false);
  useEffect(() => {
    const controller = new AbortController(); let live = true;
    setProcessing(null); setProcessingError(false);
    api.requestJson(bandWorkspacePaths.personalLogProcessing(band.id, log.id), requestOptions(controller.signal)).then((response) => {
      if (live && !controller.signal.aborted) setProcessing(response?.processing ?? null);
    }).catch(() => { if (live && !controller.signal.aborted) setProcessingError(true); });
    return () => { live = false; controller.abort(); };
  }, [api, band.id, log.id, workspaceKey]);
  const source = log.file_type === 'video' ? (log.playback?.video_url || log.playback?.url) : (log.playback?.audio_url || log.playback?.url);
  const refreshExpiredUrl = () => { if (!staleAttempt.current) { staleAttempt.current = true; onRefresh(); } };
  return <article className="workspace-media"><div className="detail-heading"><h3>{log.title || '개인 연습 기록'}</h3><BandDeleteAction band={band} workspaceKey={`${workspaceKey}:log:${log.id}`} canDelete={log.can_delete} label="개인 연습 기록" path={bandWorkspacePaths.personalLog(band.id, log.id)} asynchronous resourceType="personal_log" onDeleted={onDeleted} /></div>{source ? log.file_type === 'video' ? <video controls preload="metadata" src={source} onError={refreshExpiredUrl}>브라우저가 영상을 지원하지 않습니다.</video> : <audio controls preload="metadata" src={source} onError={refreshExpiredUrl}>브라우저가 오디오를 지원하지 않습니다.</audio> : <p className="workspace-muted">현재 재생 가능한 파일이 없습니다.</p>}
    {processing?.status && <p className="workspace-muted">처리 상태: {processing.status === 'failed' ? '처리에 실패했습니다.' : processing.status}</p>}{processingError && <p className="workspace-muted">처리 상태를 확인하지 못했습니다.</p>}
  </article>;
};

export const GalleryWorkspace = () => {
  const { band, api, workspaceKey } = useWorkspace();
  const fetchGallery = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.gallery(band.id, page), requestOptions(signal));
    return { entries: response?.gallery, pagination: response?.pagination };
  }, [api, band.id]);
  const gallery = usePagedRead('gallery', fetchGallery);
  const [featured, setFeatured] = useState(null);
  const staleAttempts = useRef(new Set());
  const featuredGate = useRef(createWorkspaceRequestGate());
  const uploadGate = useRef(createWorkspaceRequestGate());
  const refreshFeatured = useCallback(() => {
    const controller = new AbortController(); let live = true;
    const token = featuredGate.current.begin(`${workspaceKey}:featured`);
    api.requestJson(bandWorkspacePaths.featuredGallery(band.id), requestOptions(controller.signal)).then((response) => {
      if (live && !controller.signal.aborted && featuredGate.current.accepts(`${workspaceKey}:featured`, token)) setFeatured(response?.gallery ?? null);
    }).catch(() => {
      // A missing or no-longer-visible featured image is an ordinary empty state.
      if (live && !controller.signal.aborted && featuredGate.current.accepts(`${workspaceKey}:featured`, token)) setFeatured(null);
    });
    return () => { live = false; controller.abort(); };
  }, [api, band.id, workspaceKey]);
  useEffect(() => { setFeatured(null); return refreshFeatured(); }, [refreshFeatured]);
  const refreshImage = (imageId, kind = 'gallery') => {
    if (!takeOneStaleRefresh(staleAttempts.current, kind, imageId)) return;
    if (kind === 'featured') refreshFeatured();
    else gallery.refresh();
  };
  const uploadGallery = useCallback(async (file, onProgress) => {
    // Capture both the band id and account-scoped workspace identity before
    // initiating. A late B completion must never refresh A's gallery.
    const capturedBandId = band.id;
    const requestKey = `${workspaceKey}:gallery-upload`;
    const token = uploadGate.current.begin(requestKey);
    const isCurrent = () => uploadGate.current.accepts(requestKey, token);
    const result = await uploadBandGalleryImageFile({
      file, bandId: capturedBandId, api, onProgress, isCurrent,
    });
    if (isCurrent()) {
      gallery.refresh();
      refreshFeatured();
    }
    return result?.gallery || result;
  }, [api, band.id, gallery, refreshFeatured, workspaceKey]);
  return <section className="workspace-page" aria-labelledby="workspace-gallery-title"><div className="workspace-page-heading"><div><h1 id="workspace-gallery-title">갤러리</h1><p>밴드에서 공개한 사진입니다.</p></div></div>
    {band.membership && String(band.membership.status || 'ACTIVE').toLowerCase() === 'active' && <BandUploadInput band={band} onUpload={uploadGallery} accept="image/*,.png,.jpg,.jpeg,.gif,.webp" multiple={true} />}
    {featured?.url && <section className="workspace-featured"><h2>대표 사진</h2><img src={featured.url} alt="대표 갤러리 이미지" onError={() => refreshImage(featured.id, 'featured')} />{!gallery.entries.some((image) => Number(image.id) === Number(featured.id)) && <BandDeleteAction band={band} workspaceKey={`${workspaceKey}:gallery:${featured.id}`} canDelete={featured.can_delete} label="갤러리 사진" path={bandWorkspacePaths.galleryImage(band.id, featured.id)} asynchronous resourceType="gallery" onDeleted={() => { gallery.refresh(); refreshFeatured(); }} />}</section>}
    {gallery.loading && gallery.entries.length === 0 ? <p aria-live="polite">갤러리를 불러오는 중입니다…</p> : gallery.error && gallery.entries.length === 0 ? <ErrorState message={genericError} retry={gallery.refresh} /> : gallery.entries.length === 0 ? <div className="workspace-empty"><p>표시할 사진이 없습니다.</p></div> : <div className="workspace-gallery-grid">{gallery.entries.map((image) => <figure className="workspace-gallery-item" key={image.id}>{image.url ? <img src={image.url} alt="갤러리 이미지" onError={() => refreshImage(image.id)} /> : <div className="workspace-gallery-missing">표시할 사진이 없습니다.</div>}<BandDeleteAction band={band} workspaceKey={`${workspaceKey}:gallery:${image.id}`} canDelete={image.can_delete} label="갤러리 사진" path={bandWorkspacePaths.galleryImage(band.id, image.id)} asynchronous resourceType="gallery" onDeleted={() => { gallery.refresh(); refreshFeatured(); }} /></figure>)}</div>}
    {gallery.pagination?.has_more && <button className="secondary-btn" disabled={gallery.loading} onClick={gallery.loadMore}>{gallery.loading ? '사진을 불러오는 중…' : '사진 더 불러오기'}</button>}
    {gallery.error && gallery.entries.length > 0 && <ErrorState message={genericError} retry={gallery.refresh} />}
  </section>;
};

export const SuggestionsWorkspace = () => {
  const { band, api, workspaceKey } = useWorkspace();
  const fetchSuggestions = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.suggestions(band.id, page), requestOptions(signal));
    return { entries: response?.suggestions, pagination: response?.pagination };
  }, [api, band.id]);
  const suggestions = usePagedRead('suggestions', fetchSuggestions);
  const member = Boolean(band.membership && String(band.membership.status || 'ACTIVE').toLowerCase() === 'active');
  const suggestionCards = suggestions.entries.map((suggestion) => <SuggestionCard key={suggestion.id} suggestion={suggestion} member={member} onChanged={suggestions.refresh} />);
  const [creating, setCreating] = useState(false);
  return <section className="workspace-page workspace-suggestions-page" aria-labelledby="workspace-suggestions-title"><div className="workspace-page-heading"><div><h1 id="workspace-suggestions-title">추천곡</h1><p>{member ? '추천곡을 등록하고 투표해 보세요.' : '공개된 추천곡을 확인할 수 있습니다.'}</p></div>{member && <button type="button" className="primary-btn workspace-suggestions-add" onClick={() => setCreating(true)}>추천곡 추가</button>}</div>
    {!member && <WriteGuidance />}{creating && <SuggestionForm key={workspaceKey} onDone={() => { setCreating(false); suggestions.refresh(); }} onCancel={() => setCreating(false)} />}
    {suggestions.loading && suggestions.entries.length === 0 ? <p aria-live="polite">추천곡을 불러오는 중입니다…</p> : suggestions.error && suggestions.entries.length === 0 ? <ErrorState message={genericError} retry={suggestions.refresh} /> : suggestions.entries.length === 0 ? <div className="workspace-empty"><p>표시할 추천곡이 없습니다.</p>{member && <button type="button" className="secondary-btn" onClick={() => setCreating(true)}>첫 추천곡 추가</button>}</div> : <div className="workspace-cards workspace-cards--suggestions">{suggestionCards}</div>}
    {suggestions.pagination?.has_more && <button className="secondary-btn" disabled={suggestions.loading} onClick={suggestions.loadMore}>{suggestions.loading ? '추천곡을 불러오는 중…' : '추천곡 더 불러오기'}</button>}
    {suggestions.error && suggestions.entries.length > 0 && <ErrorState message={genericError} retry={suggestions.refresh} />}
  </section>;
};

const SuggestionForm = ({ item, onDone, onCancel }) => {
  const { band, api } = useWorkspace(); const [value, setValue] = useState({ title: item?.title || '', artist: item?.artist || '', link: item?.link || '', memo: item?.memo || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS' }); const [saving, setSaving] = useState(false); const [error, setError] = useState(''); const flight = useRef(false);
  const save = async (event) => { event.preventDefault(); if (flight.current) return; flight.current = true; setSaving(true); setError(''); try { await api.requestJson(item ? bandWorkspacePaths.suggestion(band.id, item.id) : bandWorkspacePaths.suggestions(band.id), { method: item ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(suggestionPayload(value)) }); onDone(); } catch { setError('추천곡을 저장하지 못했습니다. 입력값을 확인해 주세요.'); setSaving(false); } finally { flight.current = false; } };
  const change = (event) => setValue((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  return <form className="workspace-form" onSubmit={save}><h2>{item ? '추천곡 수정' : '추천곡 추가'}</h2><label>제목<input required name="title" value={value.title} onChange={change} /></label><label>아티스트<input required name="artist" value={value.artist} onChange={change} /></label><label>참고 링크<input required type="url" name="link" value={value.link} onChange={change} /></label><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label><label>공개 범위<select name="visibility" value={value.visibility} onChange={change}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={onCancel}>취소</button><button className="primary-btn" disabled={saving}>{saving ? '저장 중…' : '저장'}</button></div></form>;
};

const SuggestionCard = ({ suggestion, member, onChanged }) => {
  const { band, api } = useWorkspace(); const [editing, setEditing] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const voteFlight = useRef(false); const writeFlight = useRef(false);
  const safeLink = safeExternalHttpUrl(suggestion.link);
  const canManage = suggestion.can_edit || suggestion.can_delete || suggestion.can_proceed;
  const vote = async (clicked) => { if (!member || voteFlight.current) return; voteFlight.current = true; setBusy(true); setError(''); try { await api.requestJson(bandWorkspacePaths.suggestionVote(band.id, suggestion.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(votePayload(suggestion.viewer_vote === clicked ? 0 : clicked)) }); onChanged(); } catch { setError('투표를 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.'); } finally { voteFlight.current = false; setBusy(false); } };
  const proceed = async () => { if (!suggestion.can_proceed || writeFlight.current || !window.confirm('이 추천곡을 곡 목록으로 진행할까요? 이 작업은 추천글을 목록에서 제거합니다.')) return; writeFlight.current = true; setBusy(true); setError(''); try { await api.requestJson(bandWorkspacePaths.suggestionProceed(band.id, suggestion.id), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ visibility: suggestion.visibility }) }); onChanged(); } catch { setError('추천곡 진행을 완료하지 못했습니다.'); } finally { writeFlight.current = false; setBusy(false); } };
  if (editing) return <SuggestionForm item={suggestion} onDone={() => { setEditing(false); onChanged(); }} onCancel={() => setEditing(false)} />;
  return <article className="workspace-card suggestion-card">
    <header className="suggestion-card-heading">
      <div className="suggestion-card-title-block">
        <h2 className="suggestion-card-title">{suggestion.title || '제목 없음'}</h2>
        <p className="suggestion-card-artist">{suggestion.artist || '아티스트 정보 없음'}</p>
      </div>
      <span className="suggestion-status">{suggestion.status === 'pending' ? '검토 대기' : suggestion.status}</span>
    </header>
    {suggestion.memo && <p className="suggestion-card-memo">{suggestion.memo}</p>}
    <div className="suggestion-card-meta"><span aria-live="polite">점수 {suggestion.vote_score ?? 0}</span></div>
    {safeLink && <a className="suggestion-card-link" href={safeLink} target="_blank" rel="noopener noreferrer">참고 링크 열기</a>}
    {member ? <div className="suggestion-actions">
      <div className="suggestion-vote-actions" role="group" aria-label="추천곡 투표">
        <button type="button" className="secondary-btn suggestion-vote-button suggestion-vote-button--positive" aria-pressed={suggestion.viewer_vote === 1} disabled={busy} onClick={() => vote(1)}>추천</button>
        <button type="button" className="secondary-btn suggestion-vote-button suggestion-vote-button--negative" aria-pressed={suggestion.viewer_vote === -1} disabled={busy} onClick={() => vote(-1)}>아쉬워요</button>
      </div>
      {canManage && <div className="suggestion-manage-actions" role="group" aria-label="추천곡 관리">
        {suggestion.can_edit && <button type="button" className="secondary-btn" disabled={busy} onClick={() => setEditing(true)}>수정</button>}
        <BandDeleteAction band={band} workspaceKey={`suggestion:${suggestion.id}`} canDelete={suggestion.can_delete} label="추천글" path={bandWorkspacePaths.suggestion(band.id, suggestion.id)} onDeleted={onChanged} />
        {suggestion.can_proceed && <button type="button" className="primary-btn" disabled={busy} onClick={proceed}>곡으로 진행</button>}
      </div>}
    </div> : <p className="workspace-muted">로그인 후 밴드에 가입하면 투표와 추천이 가능합니다.</p>}
    {error && <p className="workspace-error" role="alert">{error}</p>}
  </article>;
};
