import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandWorkspacePaths, rehearsalPayload, songPayload } from '../../services/bandWorkspaceApi.js';
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

export const BandWorkspace = () => {
  const { bandSlug } = useParams();
  const navigate = useNavigate();
  const { user, getAccessToken, refreshSession, signOut } = useAuth();
  const { activeBands, selectedBand, setSelectedBandIdentity } = useBands();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [state, setState] = useState({ loading: true, error: '', band: null });
  const requestVersion = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const version = ++requestVersion.current;
    setState({ loading: true, error: '', band: null });
    api.requestJson(bandWorkspacePaths.bandBySlug(bandSlug), requestOptions(controller.signal))
      .then((response) => {
        if (controller.signal.aborted || version !== requestVersion.current || !response?.band) return;
        setSelectedBandIdentity(response.band);
        setState({ loading: false, error: '', band: response.band });
      })
      .catch(() => {
        if (!controller.signal.aborted && version === requestVersion.current) {
          setState({ loading: false, error: '밴드를 찾을 수 없거나 볼 권한이 없습니다.', band: null });
        }
      });
    return () => controller.abort();
  }, [api, bandSlug, user?.id, setSelectedBandIdentity]);

  const switchBand = (event) => {
    const band = activeBands.find((item) => String(item.id) === event.target.value);
    if (band?.slug) navigate(`/bands/${encodeURIComponent(band.slug)}`);
  };

  if (state.loading) return <main className="band-workspace-loading" aria-live="polite">밴드 공간을 여는 중입니다…</main>;
  if (state.error || !state.band) return <main className="band-workspace-loading" role="alert"><p>{state.error}</p><button className="secondary-btn" onClick={() => navigate('/bands')}>내 밴드로 이동</button></main>;
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
        </nav>
        <div className="workspace-actions">
          {switchable && <label className="band-switcher">밴드 전환<select value={String(activeBands.some((item) => item.id === selectedBand?.id) ? selectedBand.id : '')} onChange={switchBand}><option value="" disabled>밴드 선택</option>{activeBands.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
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
  const [state, setState] = useState({ loading: true, error: '', songs: null, rehearsals: null });
  useEffect(() => {
    const controller = new AbortController(); let live = true;
    setState({ loading: true, error: '', songs: null, rehearsals: null });
    Promise.all([
      api.requestJson(bandWorkspacePaths.songs(band.id, { limit: 1, offset: 0 }), requestOptions(controller.signal)),
      api.requestJson(bandWorkspacePaths.rehearsals(band.id, { limit: 1, offset: 0 }), requestOptions(controller.signal)),
    ]).then(([songs, rehearsals]) => { if (live) setState({ loading: false, error: '', songs: songs.pagination?.total ?? 0, rehearsals: rehearsals.pagination?.total ?? 0 }); })
      .catch(() => { if (live) setState({ loading: false, error: genericError, songs: null, rehearsals: null }); });
    return () => { live = false; controller.abort(); };
  }, [api, band.id, workspaceKey]);
  return <section className="workspace-page" aria-labelledby="band-dashboard-title"><h1 id="band-dashboard-title">{band.name}</h1><p className="workspace-description">{band.description || '밴드의 곡과 합주 기록을 한곳에서 확인하세요.'}</p>
    {state.loading ? <p aria-live="polite">통계를 불러오는 중입니다…</p> : state.error ? <ErrorState message={state.error} /> : <div className="workspace-stats"><div><strong>{state.songs}</strong><span>공개된 곡</span></div><div><strong>{state.rehearsals}</strong><span>공개된 합주</span></div><div><strong>{band.membership ? '멤버' : '방문자'}</strong><span>현재 접근 권한</span></div></div>}
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
    {loading && entries.length === 0 ? <p aria-live="polite">{label}을 불러오는 중입니다…</p> : error ? <ErrorState message={error} retry={refresh} /> : entries.length === 0 ? <div className="workspace-empty"><p>표시할 {label}이 없습니다.</p>{member && <button className="secondary-btn" onClick={() => setFormMode('create')}>첫 {label} 추가</button>}</div> : <div className="workspace-grid"><div className="workspace-list"><ul>{entries.map((entry) => <li key={entry.id}><button className={selected?.id === entry.id ? 'workspace-row selected' : 'workspace-row'} onClick={() => { setSelected(entry); setFormMode(null); }}><strong>{entry.title}</strong><span>{kind === 'songs' ? entry.artist : entry.date}</span><small>{entry.effective_visibility === 'MEMBERS' ? '멤버 전용' : '공개'} · {entry.status || (entry.songs?.length ? `${entry.songs.length}곡` : '')}</small></button></li>)}</ul>{pagination?.has_more && <button className="secondary-btn" disabled={loading} onClick={loadMore}>{loading ? '불러오는 중…' : '더 불러오기'}</button>}</div>
      <div className="workspace-detail">{formMode ? <ContentForm kind={kind} item={formMode === 'edit' ? selected : null} onDone={() => { setFormMode(null); setSelected(null); refresh(); }} onCancel={() => setFormMode(null)} /> : selected ? <ContentDetail kind={kind} item={selected} onEdit={() => setFormMode('edit')} onDeleted={() => { setSelected(null); refresh(); }} /> : <p className="workspace-placeholder">항목을 선택하면 상세 정보와 재생 미디어를 볼 수 있습니다.</p>}</div></div>}
  </section>;
};

const ContentDetail = ({ kind, item, onEdit, onDeleted }) => {
  const { band, api, workspaceKey } = useWorkspace(); const [mediaState, setMediaState] = useState({ loading: true, error: '', media: [] }); const [deleting, setDeleting] = useState(false);
  useEffect(() => { const controller = new AbortController(); let live = true; setMediaState({ loading: true, error: '', media: [] }); api.requestJson(kind === 'songs' ? bandWorkspacePaths.songMedia(band.id, item.id) : bandWorkspacePaths.rehearsalMedia(band.id, item.id), requestOptions(controller.signal)).then((response) => { if (live) setMediaState({ loading: false, error: '', media: response?.media || [] }); }).catch(() => { if (live) setMediaState({ loading: false, error: '미디어를 불러오지 못했습니다.', media: [] }); }); return () => { live = false; controller.abort(); }; }, [api, band.id, item.id, kind, workspaceKey]);
  const remove = async () => { if (!window.confirm(`이 ${kind === 'songs' ? '곡' : '합주'}을 삭제할까요?`)) return; setDeleting(true); try { await api.requestJson(kind === 'songs' ? bandWorkspacePaths.song(band.id, item.id) : bandWorkspacePaths.rehearsal(band.id, item.id), { method: 'DELETE' }); onDeleted(); } catch { setDeleting(false); } };
  return <article><div className="detail-heading"><div><h2>{item.title}</h2><p>{kind === 'songs' ? item.artist : `${item.date}${item.time ? ` · ${item.time}` : ''}`}</p></div>{item.can_edit && <button className="secondary-btn" onClick={onEdit}>수정</button>}{item.can_delete && <button className="danger-btn" disabled={deleting} onClick={remove}>{deleting ? '삭제 중…' : '삭제'}</button>}</div>
    {kind === 'songs' ? <dl><dt>상태</dt><dd>{item.status || '-'}</dd><dt>장르</dt><dd>{item.genre || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl> : <dl><dt>장소</dt><dd>{item.location || '-'}</dd><dt>연결된 곡</dt><dd>{item.songs?.map((song) => song.title).join(', ') || '-'}</dd><dt>메모</dt><dd>{item.memo || '-'}</dd></dl>}
    <h3>재생 미디어</h3>{mediaState.loading ? <p>미디어를 불러오는 중입니다…</p> : mediaState.error ? <p className="workspace-muted">{mediaState.error}</p> : mediaState.media.length === 0 ? <p className="workspace-muted">표시할 미디어가 없습니다. 업로드는 준비 중입니다.</p> : <div className="media-list">{mediaState.media.map((media) => <PlaybackMedia key={media.id} media={media} />)}</div>}
  </article>;
};

const PlaybackMedia = ({ media }) => { const [mode, setMode] = useState(media.playback?.video_url ? 'video' : 'audio'); const source = mode === 'audio' ? media.playback?.audio_url : (media.playback?.video_url || media.playback?.url); return <section className="workspace-media"><div><strong>{media.file_type === 'video' ? '720p 영상' : '파일'} </strong><span>점수 {media.vote_score ?? 0}</span></div>{media.playback?.video_url && media.playback?.audio_url && <div className="media-modes"><button className={mode === 'video' ? 'active' : ''} onClick={() => setMode('video')}>720p 영상</button><button className={mode === 'audio' ? 'active' : ''} onClick={() => setMode('audio')}>오디오</button></div>}{source ? mode === 'video' ? <video controls preload="metadata" src={source}>브라우저가 영상을 지원하지 않습니다.</video> : <audio controls preload="metadata" src={source}>브라우저가 오디오를 지원하지 않습니다.</audio> : <p className="workspace-muted">현재 재생 가능한 변환본이 없습니다.</p>}</section>; };

const ContentForm = ({ kind, item, onDone, onCancel }) => {
  const { band, api } = useWorkspace(); const [saving, setSaving] = useState(false); const [error, setError] = useState(''); const [songs, setSongs] = useState([]);
  const initial = kind === 'songs' ? { title: item?.title || '', artist: item?.artist || '', status: item?.status || 'Practice', chords: item?.chords || '', link: item?.link || '', memo: item?.memo || '', genre: item?.genre || '', difficulty: item?.difficulty || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS' } : { title: item?.title || '', date: item?.date || '', start_date: item?.start_date || '', end_date: item?.end_date || '', time: item?.time || '', location: item?.location || '', memo: item?.memo || '', color: item?.color || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS', song_ids: item?.songs?.map((song) => String(song.id)) || [] };
  const [value, setValue] = useState(initial);
  useEffect(() => { if (kind !== 'rehearsals') return undefined; const controller = new AbortController(); api.requestJson(bandWorkspacePaths.songs(band.id, { limit: 100, offset: 0 }), requestOptions(controller.signal)).then((response) => setSongs(response?.songs || [])).catch(() => setSongs([])); return () => controller.abort(); }, [api, band.id, kind]);
  const change = (event) => setValue((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  const save = async (event) => { event.preventDefault(); if (saving) return; setSaving(true); setError(''); try { const path = kind === 'songs' ? (item ? bandWorkspacePaths.song(band.id, item.id) : bandWorkspacePaths.songs(band.id)) : (item ? bandWorkspacePaths.rehearsal(band.id, item.id) : bandWorkspacePaths.rehearsals(band.id)); await api.requestJson(path, { method: item ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(kind === 'songs' ? songPayload(value) : rehearsalPayload(value)) }); onDone(); } catch { setError('저장하지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.'); setSaving(false); } };
  return <form className="workspace-form" onSubmit={save}><h2>{item ? '수정' : '새로 추가'}</h2><label>제목<input required name="title" value={value.title} onChange={change} /></label>{kind === 'songs' ? <><label>아티스트<input required name="artist" value={value.artist} onChange={change} /></label><label>상태<select name="status" value={value.status} onChange={change}><option>Practice</option><option>Completed</option><option>OnHold</option></select></label><label>장르<input name="genre" value={value.genre} onChange={change} /></label><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label></> : <><label>날짜<input required type="date" name="date" value={value.date} onChange={change} /></label><label>시간<input name="time" value={value.time} onChange={change} /></label><label>장소<input name="location" value={value.location} onChange={change} /></label><fieldset><legend>연결할 곡</legend>{songs.map((song) => <label className="check-row" key={song.id}><input type="checkbox" checked={value.song_ids.includes(String(song.id))} onChange={(event) => setValue((previous) => ({ ...previous, song_ids: event.target.checked ? [...previous.song_ids, String(song.id)] : previous.song_ids.filter((id) => id !== String(song.id)) }))} />{song.title} — {song.artist}</label>)}</fieldset><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label></>}<label>공개 범위<select name="visibility" value={value.visibility} onChange={change}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={onCancel}>취소</button><button className="primary-btn" disabled={saving}>{saving ? '저장 중…' : '저장'}</button></div></form>;
};
