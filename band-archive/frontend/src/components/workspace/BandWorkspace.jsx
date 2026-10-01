import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandWorkspacePaths, rehearsalPayload, songPayload } from '../../services/bandWorkspaceApi.js';
import { safeExternalHttpUrl } from '../../services/safeExternalUrl.js';
import { MediaEngagement } from './MediaEngagement.jsx';
import { createWorkspaceRequestGate, mergeMediaPage, takeOneStaleRefresh, WorkspacePaintGate, workspaceIdentityKey } from './workspaceRouteState.js';
import logo from '../../assets/logo.png';
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

const requestOptions = (signal) => ({ authenticated: false, optionalAuth: true, signal });
const genericError = '정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.';

// React Router reuses a route element when only params change. Keying this
// boundary makes the old workspace (including open forms/details) unmount
// synchronously before the next slug or account can render.
export const BandWorkspaceRoute = () => {
  const { bandSlug } = useParams();
  const { user } = useAuth();
  return <BandWorkspace key={workspaceIdentityKey(bandSlug, user?.id)} />;
};

export const BandWorkspace = () => {
  const { bandSlug } = useParams();
  const navigate = useNavigate();
  const { user, getAccessToken, refreshSession, signOut } = useAuth();
  const { activeBands } = useBands();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [state, setState] = useState({ loading: true, error: '', band: null });
  const requestGate = useRef(createWorkspaceRequestGate());
  const routeKey = workspaceIdentityKey(bandSlug, user?.id);

  useEffect(() => {
    const controller = new AbortController();
    const version = requestGate.current.begin(routeKey);
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
    return () => controller.abort();
  }, [api, bandSlug, routeKey]);

  const switchBand = (event) => {
    const band = activeBands.find((item) => String(item.id) === event.target.value);
    if (band?.slug) navigate(`/bands/${encodeURIComponent(band.slug)}`);
  };

  if (state.loading) return <WorkspacePaintGate requestedSlug={bandSlug} />;
  if (state.error || !state.band) return <main className="band-workspace-loading" role="alert"><p>{state.error}</p><button className="secondary-btn" onClick={() => navigate('/bands')}>내 밴드로 이동</button></main>;
  if (state.band.slug !== bandSlug) return <WorkspacePaintGate requestedSlug={bandSlug} resolvedSlug={state.band.slug} />;
  const value = { band: state.band, api, workspaceKey: `${state.band.id}:${user?.id || 'visitor'}` };
  const switchable = activeBands.length > 1;
  return <WorkspaceContext.Provider value={value}>
    <div className="band-workspace">
      <header className="workspace-header">
        <button className="workspace-brand" onClick={() => navigate(`/bands/${encodeURIComponent(state.band.slug)}`)}>
          <img src={logo} alt="들뜬" /><span>{state.band.name}<small> Archive</small></span>
        </button>
        <nav aria-label="밴드 공간">
          <NavLink end to={`/bands/${encodeURIComponent(state.band.slug)}`}>대시보드</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/songs`}>곡</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/rehearsals`}>합주</NavLink>
          {state.band.permissions?.can_view_roster && <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/members`}>멤버</NavLink>}
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/gallery`}>갤러리</NavLink>
          <NavLink to={`/bands/${encodeURIComponent(state.band.slug)}/suggestions`}>추천곡</NavLink>
        </nav>
        <div className="workspace-actions">
          {switchable && <label className="band-switcher">밴드 전환<select value={String(activeBands.some((item) => item.id === state.band.id) ? state.band.id : '')} onChange={switchBand}><option value="" disabled>밴드 선택</option>{activeBands.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
          <button className="nav-btn" onClick={() => navigate('/bands')}>내 밴드</button>
          {user ? <button className="nav-btn" onClick={async () => { await signOut(); navigate('/', { replace: true }); }}>로그아웃</button> : <button className="nav-btn" onClick={() => navigate('/login')}>로그인</button>}
        </div>
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
  const [state, setState] = useState({ loading: true, error: '', songs: null, rehearsals: null, announcement: null });
  useEffect(() => {
    const controller = new AbortController(); let live = true;
    setState({ loading: true, error: '', songs: null, rehearsals: null, announcement: null });
    Promise.allSettled([
      api.requestJson(bandWorkspacePaths.songs(band.id, { limit: 1, offset: 0 }), requestOptions(controller.signal)),
      api.requestJson(bandWorkspacePaths.rehearsals(band.id, { limit: 1, offset: 0 }), requestOptions(controller.signal)),
      api.requestJson(bandWorkspacePaths.announcement(band.id), requestOptions(controller.signal)),
    ]).then(([songs, rehearsals, announcement]) => {
      if (!live) return;
      if (songs.status !== 'fulfilled' || rehearsals.status !== 'fulfilled') {
        setState({ loading: false, error: genericError, songs: null, rehearsals: null, announcement: null });
        return;
      }
      setState({ loading: false, error: '', songs: songs.value.pagination?.total ?? 0, rehearsals: rehearsals.value.pagination?.total ?? 0, announcement: announcement.status === 'fulfilled' ? announcement.value?.announcement ?? null : null });
    });
    return () => { live = false; controller.abort(); };
  }, [api, band.id, workspaceKey]);
  return <section className="workspace-page" aria-labelledby="band-dashboard-title"><h1 id="band-dashboard-title">{band.name}</h1><p className="workspace-description">{band.description || '밴드의 곡과 합주 기록을 한곳에서 확인하세요.'}</p>
    {state.loading ? <p aria-live="polite">통계를 불러오는 중입니다…</p> : state.error ? <ErrorState message={state.error} /> : <><div className="workspace-stats"><div><strong>{state.songs}</strong><span>{band.membership ? '이 밴드의 곡' : '공개된 곡'}</span></div><div><strong>{state.rehearsals}</strong><span>{band.membership ? '이 밴드의 합주' : '공개된 합주'}</span></div><div><strong>{band.membership ? '멤버' : '방문자'}</strong><span>현재 접근 권한</span></div></div><AnnouncementSummary announcement={state.announcement} /></>}
    <WriteGuidance /></section>;
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
  return <ContentWorkspace key={`${workspaceKey}:${band.id}:rehearsals`} kind="rehearsals" />;
};

const ContentWorkspace = ({ kind }) => {
  const { band } = useWorkspace();
  const { loading, error, entries, pagination, refresh, loadMore } = useCollection(kind);
  const [selected, setSelected] = useState(null); const [formMode, setFormMode] = useState(null);
  const member = Boolean(band.membership);
  const label = kind === 'songs' ? '곡' : '합주';
  return <section className="workspace-page content-workspace" aria-labelledby={`${kind}-title`}>
    <div className="workspace-page-heading"><div><h1 id={`${kind}-title`}>{label} 목록</h1><p>{member ? `${label}을 관리하고 재생 미디어를 확인하세요.` : `공개된 ${label}만 표시합니다.`}</p></div>{member && <button className="primary-btn" onClick={() => { setSelected(null); setFormMode('create'); }}>{label} 추가</button>}</div>
    {!member && <WriteGuidance />}
    {loading && entries.length === 0 ? <p aria-live="polite">{label}을 불러오는 중입니다…</p> : error ? <ErrorState message={error} retry={refresh} /> : entries.length === 0 && !formMode ? <div className="workspace-empty"><p>표시할 {label}이 없습니다.</p>{member && <button className="secondary-btn" onClick={() => setFormMode('create')}>첫 {label} 추가</button>}</div> : <div className="workspace-grid"><div className="workspace-list"><ul>{entries.map((entry) => <li key={entry.id}><button className={selected?.id === entry.id ? 'workspace-row selected' : 'workspace-row'} onClick={() => { setSelected(entry); setFormMode(null); }}><strong>{entry.title}</strong><span>{kind === 'songs' ? entry.artist : entry.date}</span><small>{entry.effective_visibility === 'MEMBERS' ? '멤버 전용' : '공개'} · {entry.status || (entry.songs?.length ? `${entry.songs.length}곡` : '')}</small></button></li>)}</ul>{pagination?.has_more && <button className="secondary-btn" disabled={loading} onClick={loadMore}>{loading ? '불러오는 중…' : '더 불러오기'}</button>}</div>
      <div className="workspace-detail">{formMode ? <ContentForm kind={kind} item={formMode === 'edit' ? selected : null} onDone={() => { setFormMode(null); setSelected(null); refresh(); }} onCancel={() => setFormMode(null)} /> : selected ? <ContentDetail kind={kind} item={selected} onEdit={() => setFormMode('edit')} onDeleted={() => { setSelected(null); refresh(); }} /> : <p className="workspace-placeholder">항목을 선택하면 상세 정보와 재생 미디어를 볼 수 있습니다.</p>}</div></div>}
  </section>;
};

const ContentDetail = ({ kind, item, onEdit, onDeleted }) => {
  const { band, api, workspaceKey } = useWorkspace(); const [mediaState, setMediaState] = useState({ loading: true, error: '', pages: [], pagination: null }); const [deleting, setDeleting] = useState(false); const [deleteError, setDeleteError] = useState('');
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
  const remove = async () => { if (!window.confirm(`이 ${kind === 'songs' ? '곡' : '합주'}을 삭제할까요?`)) return; setDeleting(true); setDeleteError(''); try { await api.requestJson(kind === 'songs' ? bandWorkspacePaths.song(band.id, item.id) : bandWorkspacePaths.rehearsal(band.id, item.id), { method: 'DELETE' }); onDeleted(); } catch { setDeleting(false); setDeleteError('삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.'); } };
  return <article><div className="detail-heading"><div><h2>{item.title}</h2><p>{kind === 'songs' ? item.artist : `${item.date}${item.time ? ` · ${item.time}` : ''}`}</p></div>{item.can_edit && <button className="secondary-btn" onClick={onEdit}>수정</button>}{item.can_delete && <button className="danger-btn" disabled={deleting} onClick={remove}>{deleting ? '삭제 중…' : '삭제'}</button>}</div>
    {deleteError && <p className="workspace-error" role="alert">{deleteError}</p>}{kind === 'songs' ? <dl><dt>상태</dt><dd>{item.status || '-'}</dd><dt>장르</dt><dd>{item.genre || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl> : <dl><dt>장소</dt><dd>{item.location || '-'}</dd><dt>연결된 곡</dt><dd>{item.songs?.map((song) => song.title).join(', ') || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl>}
    <h3>재생 미디어</h3>{mediaState.loading && mediaState.pages.length === 0 ? <p>미디어를 불러오는 중입니다…</p> : mediaState.error && mediaState.pages.length === 0 ? <p className="workspace-muted">{mediaState.error}</p> : mediaState.pages.length === 0 ? <p className="workspace-muted">표시할 미디어가 없습니다. 업로드는 준비 중입니다.</p> : <><div className="media-list">{mediaState.pages.flatMap((page) => page.media.map((media) => <PlaybackMedia key={media.id} media={media} band={band} api={api} onMediaUpdate={updateMedia} onAuthoritativeRefresh={refreshAuthoritativeMedia} onStale={() => { if (takeOneStaleRefresh(staleAttempts.current, page.offset, media.id)) loadMedia(page.offset); }} />))}</div>{mediaState.pagination?.has_more && <button className="secondary-btn" disabled={mediaState.loading} onClick={() => loadMedia(mediaState.pagination.next_offset)}>{mediaState.loading ? '미디어를 불러오는 중…' : '미디어 더 불러오기'}</button>}{mediaState.error && <p className="workspace-muted" role="alert">{mediaState.error}</p>}</>}
  </article>;
};

const PlaybackMedia = ({ media, band, api, onMediaUpdate, onAuthoritativeRefresh, onStale }) => { const [mode, setMode] = useState(media.playback?.video_url ? 'video' : 'audio'); const [failed, setFailed] = useState(false); const source = mode === 'audio' ? media.playback?.audio_url : (media.playback?.video_url || media.playback?.url); const stale = () => { setFailed(true); onStale(); }; return <section className="workspace-media"><div><strong>{media.file_type === 'video' ? '720p 영상' : '파일'} </strong></div>{media.playback?.video_url && media.playback?.audio_url && <div className="media-modes"><button className={mode === 'video' ? 'active' : ''} onClick={() => { setFailed(false); setMode('video'); }}>720p 영상</button><button className={mode === 'audio' ? 'active' : ''} onClick={() => { setFailed(false); setMode('audio'); }}>오디오</button></div>}{source ? mode === 'video' ? <video controls preload="metadata" src={source} onError={stale}>브라우저가 영상을 지원하지 않습니다.</video> : <audio controls preload="metadata" src={source} onError={stale}>브라우저가 오디오를 지원하지 않습니다.</audio> : <p className="workspace-muted">현재 재생 가능한 변환본이 없습니다.</p>}{failed && <p className="workspace-muted">재생 주소를 한 번 새로고침했습니다. 계속 실패하면 잠시 후 다시 시도해 주세요.</p>}<MediaEngagement band={band} api={api} media={media} onMediaUpdate={onMediaUpdate} onAuthoritativeRefresh={onAuthoritativeRefresh} /></section>; };

const ContentForm = ({ kind, item, onDone, onCancel }) => {
  const { band, api } = useWorkspace(); const [saving, setSaving] = useState(false); const savingRef = useRef(false); const [error, setError] = useState(''); const [songs, setSongs] = useState([]);
  const initial = kind === 'songs' ? { title: item?.title || '', artist: item?.artist || '', status: item?.status || 'Practice', chords: item?.chords || '', link: item?.link || '', memo: item?.memo || '', genre: item?.genre || '', difficulty: item?.difficulty || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS' } : { title: item?.title || '', date: item?.date || '', start_date: item?.start_date || '', end_date: item?.end_date || '', time: item?.time || '', location: item?.location || '', memo: item?.memo || '', color: item?.color || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS', song_ids: item?.songs?.map((song) => String(song.id)) || [] };
  const [value, setValue] = useState(initial);
  useEffect(() => { if (kind !== 'rehearsals') return undefined; const controller = new AbortController(); api.requestJson(bandWorkspacePaths.songs(band.id, { limit: 100, offset: 0 }), requestOptions(controller.signal)).then((response) => setSongs(response?.songs || [])).catch(() => setSongs([])); return () => controller.abort(); }, [api, band.id, kind]);
  const change = (event) => setValue((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  const save = async (event) => { event.preventDefault(); if (savingRef.current) return; savingRef.current = true; setSaving(true); setError(''); try { const path = kind === 'songs' ? (item ? bandWorkspacePaths.song(band.id, item.id) : bandWorkspacePaths.songs(band.id)) : (item ? bandWorkspacePaths.rehearsal(band.id, item.id) : bandWorkspacePaths.rehearsals(band.id)); await api.requestJson(path, { method: item ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(kind === 'songs' ? songPayload(value) : rehearsalPayload(value)) }); onDone(); } catch { setError('저장하지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.'); setSaving(false); } finally { savingRef.current = false; } };
  return <form className="workspace-form" onSubmit={save}><h2>{item ? '수정' : '새로 추가'}</h2><label>제목<input required name="title" value={value.title} onChange={change} /></label>{kind === 'songs' ? <><label>아티스트<input required name="artist" value={value.artist} onChange={change} /></label><label>상태<select name="status" value={value.status} onChange={change}><option>Practice</option><option>Completed</option><option>OnHold</option></select></label><label>장르<input name="genre" value={value.genre} onChange={change} /></label><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label></> : <><label>날짜<input required type="date" name="date" value={value.date} onChange={change} /></label><label>시간<input name="time" value={value.time} onChange={change} /></label><label>장소<input name="location" value={value.location} onChange={change} /></label><fieldset><legend>연결할 곡</legend>{songs.map((song) => <label className="check-row" key={song.id}><input type="checkbox" checked={value.song_ids.includes(String(song.id))} onChange={(event) => setValue((previous) => ({ ...previous, song_ids: event.target.checked ? [...previous.song_ids, String(song.id)] : previous.song_ids.filter((id) => id !== String(song.id)) }))} />{song.title} — {song.artist}</label>)}</fieldset><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label></>}<label>공개 범위<select name="visibility" value={value.visibility} onChange={change}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={onCancel}>취소</button><button className="primary-btn" disabled={saving}>{saving ? '저장 중…' : '저장'}</button></div></form>;
};

const AnnouncementSummary = ({ announcement }) => (
  <section className="workspace-announcement" aria-label="공지사항">
    <h2>공지사항</h2>
    {announcement?.content ? <p>{announcement.content}</p> : <p className="workspace-muted">현재 공개된 공지사항이 없습니다.</p>}
  </section>
);

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
  const { band, api } = useWorkspace();
  const fetchMembers = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.members(band.id, page), requestOptions(signal));
    return { entries: response?.members, pagination: response?.pagination };
  }, [api, band.id]);
  const { loading, error, notFound, entries, pagination, refresh, loadMore } = usePagedRead('members', fetchMembers);
  const rosterUnavailable = !loading && notFound;
  if (rosterUnavailable) return <RosterUnavailable />;
  return <section className="workspace-page" aria-labelledby="workspace-members-title"><div className="workspace-page-heading"><div><h1 id="workspace-members-title">멤버</h1><p>밴드에서 공개한 멤버와 연습 기록입니다.</p></div></div>
    {loading && entries.length === 0 ? <p aria-live="polite">멤버를 불러오는 중입니다…</p> : error && entries.length === 0 ? <ErrorState message={genericError} retry={refresh} /> : entries.length === 0 ? <div className="workspace-empty"><p>표시할 멤버가 없습니다.</p></div> : <div className="workspace-cards">{entries.map((member) => <NavLink className="workspace-card workspace-card-link" key={member.id} to={`/bands/${encodeURIComponent(band.slug)}/members/${member.id}`}><strong>{member.name || '멤버'}</strong><span>{member.instrument || '담당 악기 정보 없음'}</span></NavLink>)}</div>}
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
  const [memberState, setMemberState] = useState({ loading: true, error: '', member: null });
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
    {memberState.loading ? <p aria-live="polite">멤버 정보를 불러오는 중입니다…</p> : <><div className="workspace-page-heading"><div><h1 id="workspace-member-title">{memberState.member.name || '멤버'}</h1><p>{memberState.member.instrument || '담당 악기 정보 없음'}</p></div><NavLink className="secondary-btn workspace-button-link" to={`/bands/${encodeURIComponent(band.slug)}/members`}>목록으로</NavLink></div>
      <h2>개인 연습 기록</h2>
      {logs.loading && logs.entries.length === 0 ? <p aria-live="polite">기록을 불러오는 중입니다…</p> : logs.entries.length === 0 ? <p className="workspace-muted">표시할 개인 연습 기록이 없습니다.</p> : <div className="workspace-grid"><div className="workspace-list"><ul>{logs.entries.map((log) => <li key={log.id}><button className={selectedLog?.id === log.id ? 'workspace-row selected' : 'workspace-row'} onClick={() => setSelectedLogId(log.id)}><strong>{log.title || '개인 연습 기록'}</strong><span>{log.created_at ? new Date(log.created_at).toLocaleString() : ''}</span><small>{log.file_type === 'video' ? '영상' : '오디오'}</small></button></li>)}</ul>{logs.pagination?.has_more && <button className="secondary-btn" disabled={logs.loading} onClick={logs.loadMore}>{logs.loading ? '기록을 불러오는 중…' : '기록 더 불러오기'}</button>}</div><div className="workspace-detail">{selectedLog ? <PersonalLogPlayback log={selectedLog} onRefresh={logs.refresh} /> : <p className="workspace-placeholder">기록을 선택하면 재생할 수 있습니다.</p>}</div></div>}
      {logs.error && <ErrorState message={genericError} retry={logs.refresh} />}</>}
  </section>;
};

const PersonalLogPlayback = ({ log, onRefresh }) => {
  const { band, api, workspaceKey } = useWorkspace();
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
  return <article className="workspace-media"><h3>{log.title || '개인 연습 기록'}</h3>{source ? log.file_type === 'video' ? <video controls preload="metadata" src={source} onError={refreshExpiredUrl}>브라우저가 영상을 지원하지 않습니다.</video> : <audio controls preload="metadata" src={source} onError={refreshExpiredUrl}>브라우저가 오디오를 지원하지 않습니다.</audio> : <p className="workspace-muted">현재 재생 가능한 파일이 없습니다.</p>}
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
  return <section className="workspace-page" aria-labelledby="workspace-gallery-title"><div className="workspace-page-heading"><div><h1 id="workspace-gallery-title">갤러리</h1><p>밴드에서 공개한 사진입니다.</p></div></div>
    {featured?.url && <section className="workspace-featured"><h2>대표 사진</h2><img src={featured.url} alt="대표 갤러리 이미지" onError={() => refreshImage(featured.id, 'featured')} /></section>}
    {gallery.loading && gallery.entries.length === 0 ? <p aria-live="polite">갤러리를 불러오는 중입니다…</p> : gallery.error && gallery.entries.length === 0 ? <ErrorState message={genericError} retry={gallery.refresh} /> : gallery.entries.length === 0 ? <div className="workspace-empty"><p>표시할 사진이 없습니다.</p></div> : <div className="workspace-gallery-grid">{gallery.entries.map((image) => image.url ? <img key={image.id} src={image.url} alt="갤러리 이미지" onError={() => refreshImage(image.id)} /> : <div className="workspace-gallery-missing" key={image.id}>표시할 사진이 없습니다.</div>)}</div>}
    {gallery.pagination?.has_more && <button className="secondary-btn" disabled={gallery.loading} onClick={gallery.loadMore}>{gallery.loading ? '사진을 불러오는 중…' : '사진 더 불러오기'}</button>}
    {gallery.error && gallery.entries.length > 0 && <ErrorState message={genericError} retry={gallery.refresh} />}
  </section>;
};

export const SuggestionsWorkspace = () => {
  const { band, api } = useWorkspace();
  const fetchSuggestions = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.suggestions(band.id, page), requestOptions(signal));
    return { entries: response?.suggestions, pagination: response?.pagination };
  }, [api, band.id]);
  const suggestions = usePagedRead('suggestions', fetchSuggestions);
  return <section className="workspace-page" aria-labelledby="workspace-suggestions-title"><div className="workspace-page-heading"><div><h1 id="workspace-suggestions-title">추천곡</h1><p>현재는 밴드가 공개한 추천 목록을 읽기만 할 수 있습니다.</p></div></div>
    {suggestions.loading && suggestions.entries.length === 0 ? <p aria-live="polite">추천곡을 불러오는 중입니다…</p> : suggestions.error && suggestions.entries.length === 0 ? <ErrorState message={genericError} retry={suggestions.refresh} /> : suggestions.entries.length === 0 ? <div className="workspace-empty"><p>표시할 추천곡이 없습니다.</p></div> : <div className="workspace-cards">{suggestions.entries.map((suggestion) => <SuggestionCard key={suggestion.id} suggestion={suggestion} />)}</div>}
    {suggestions.pagination?.has_more && <button className="secondary-btn" disabled={suggestions.loading} onClick={suggestions.loadMore}>{suggestions.loading ? '추천곡을 불러오는 중…' : '추천곡 더 불러오기'}</button>}
    {suggestions.error && suggestions.entries.length > 0 && <ErrorState message={genericError} retry={suggestions.refresh} />}
  </section>;
};

const SuggestionCard = ({ suggestion }) => {
  const safeLink = safeExternalHttpUrl(suggestion.link);
  return <article className="workspace-card"><strong>{suggestion.title || '제목 없음'}</strong><span>{suggestion.artist || '아티스트 정보 없음'}</span>{suggestion.memo && <p>{suggestion.memo}</p>}<small>점수 {suggestion.vote_score ?? 0} · {suggestion.status === 'pending' ? '검토 대기' : suggestion.status}</small>{safeLink && <a href={safeLink} target="_blank" rel="noopener noreferrer">참고 링크 열기</a>}</article>;
};
