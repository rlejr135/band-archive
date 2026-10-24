import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import { FeaturedMediaEditor } from '../workspace/featured-media/FeaturedMediaEditor.jsx';
import './BandsPage.css';

const PAGE_SIZE = 20;

const pageState = (identity, loading = true) => ({ identity, loading, error: '', band: null, songs: [], pagination: null });

const managementError = (error) => {
  if (error?.status === 401) return '로그인 세션을 확인할 수 없습니다. 다시 로그인해 주세요.';
  if (error?.status === 403) return '이 밴드의 대표 미디어를 관리할 권한이 없습니다.';
  if (error?.status === 404) return '밴드를 찾을 수 없거나 대표 미디어를 관리할 권한이 없습니다.';
  return '대표 미디어 관리 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.';
};

const parseCatalog = (data, slug) => {
  const band = data?.band;
  const pagination = data?.pagination;
  if (!band || !Number.isSafeInteger(band.id) || band.id <= 0 || band.slug !== slug
    || typeof band.name !== 'string' || !band.name.trim() || band.can_feature !== true
    || !Array.isArray(data.songs)) return null;
  const songs = data.songs.filter((song) => Number.isSafeInteger(song?.id) && song.id > 0
    && typeof song.title === 'string' && song.can_feature === true)
    .map((song) => ({ id: song.id, title: song.title, artist: typeof song.artist === 'string' ? song.artist : '' }));
  return {
    band: { id: band.id, slug: band.slug, name: band.name },
    songs,
    pagination: {
      limit: Number.isInteger(pagination?.limit) && pagination.limit > 0 ? pagination.limit : PAGE_SIZE,
      offset: Number.isInteger(pagination?.offset) && pagination.offset >= 0 ? pagination.offset : 0,
      total: Number.isInteger(pagination?.total) && pagination.total >= 0 ? pagination.total : songs.length,
      has_more: pagination?.has_more === true,
      next_offset: Number.isInteger(pagination?.next_offset) && pagination.next_offset >= 0 ? pagination.next_offset : null,
    },
  };
};

const mergeSongs = (previous, incoming) => {
  const byId = new Map(previous.map((song) => [String(song.id), song]));
  incoming.forEach((song) => byId.set(String(song.id), song));
  return [...byId.values()];
};

export const RepresentativeMediaManagement = ({ bandSlug, identity: suppliedIdentity }) => {
  const { user, getAccessToken, refreshSession } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const identity = suppliedIdentity || `${user?.id || 'anonymous'}:${bandSlug}`;
  const [state, setState] = useState(() => pageState(identity));
  const [selectedSongId, setSelectedSongId] = useState('');
  const [mutationError, setMutationError] = useState('');
  const sequence = useRef(0);
  const controllerRef = useRef(null);
  const identityRef = useRef(identity);
  const mountedRef = useRef(false);
  identityRef.current = identity;

  const loadPage = useCallback(async (offset = 0, { append = false, initial = false } = {}) => {
    if (!mountedRef.current) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const requestSequence = ++sequence.current;
    if (!initial) setState((previous) => ({ ...previous, identity, loading: true, error: '' }));
    try {
      const response = await api.requestJson(bandWorkspacePaths.representativeMediaManagement(bandSlug, { limit: PAGE_SIZE, offset }), { signal: controller.signal });
      const parsed = parseCatalog(response, bandSlug);
      if (!parsed) throw new Error('Invalid representative media management response');
      if (controller.signal.aborted || requestSequence !== sequence.current || identityRef.current !== identity) return;
      setState((previous) => ({ identity, loading: false, error: '', band: parsed.band,
        songs: append && previous.identity === identity ? mergeSongs(previous.songs, parsed.songs) : parsed.songs,
        pagination: parsed.pagination }));
      if (!append) setSelectedSongId((previous) => previous && parsed.songs.some((song) => String(song.id) === previous) ? previous : '');
    } catch (error) {
      if (mountedRef.current && !controller.signal.aborted && requestSequence === sequence.current && identityRef.current === identity) {
        setState((previous) => append && previous.identity === identity
          ? { ...previous, loading: false, error: managementError(error) }
          : { ...pageState(identity, false), error: managementError(error) });
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }, [api, bandSlug, identity]);

  useEffect(() => {
    mountedRef.current = true;
    loadPage(0, { initial: true });
    return () => {
      mountedRef.current = false;
      sequence.current += 1;
      controllerRef.current?.abort();
      controllerRef.current = null;
    };
  }, [loadPage]);

  const activeState = state.identity === identity ? state : pageState(identity);
  const selectedSong = activeState.songs.find((song) => String(song.id) === selectedSongId);
  const selectedBand = activeState.band;

  return <main className="bands-page manage-page representative-media-management" aria-labelledby="representative-media-management-title">
    <Link className="bands-return-link" to="/account"><span aria-hidden="true">←</span> 계정과 밴드</Link>
    <header className="bands-manage-header"><div><p className="account-eyebrow">FEATURED MEDIA</p><h1 id="representative-media-management-title">대표 미디어 관리</h1>{selectedBand && <p>{selectedBand.name}</p>}</div></header>
    {mutationError && <p className="auth-error" role="alert">{mutationError}</p>}
    {activeState.loading && !activeState.band ? <p aria-live="polite">대표 미디어 관리 정보를 불러오는 중입니다…</p>
      : activeState.error && !activeState.band ? <section className="bands-panel" role="alert"><p>{activeState.error}</p><button className="secondary-btn" type="button" onClick={() => loadPage()}>다시 시도</button></section>
        : <>
          <section className="bands-panel" aria-labelledby="representative-management-song-title">
            <h2 id="representative-management-song-title">곡 선택</h2>
            <p className="bands-help">밴드 관리자 권한으로 해당 곡의 대표 영상 또는 음원을 지정하거나 해제할 수 있습니다.</p>
            {activeState.songs.length === 0 && !activeState.loading ? <p className="bands-help">대표 미디어를 관리할 수 있는 곡이 없습니다.</p> : <label htmlFor="representative-management-song-select">관리할 곡<select id="representative-management-song-select" value={selectedSongId} onChange={(event) => { setSelectedSongId(event.target.value); setMutationError(''); }}>
              <option value="">곡을 선택해 주세요</option>{activeState.songs.map((song) => <option key={song.id} value={song.id}>{song.title}{song.artist ? ` · ${song.artist}` : ''}</option>)}
            </select></label>}
            {activeState.pagination?.has_more && <button type="button" className="secondary-btn" disabled={activeState.loading} onClick={() => loadPage(activeState.pagination.next_offset ?? activeState.songs.length, { append: true })}>{activeState.loading ? '곡을 불러오는 중…' : '곡 더 불러오기'}</button>}
            {activeState.error && <p role="alert" className="auth-error">{activeState.error}</p>}
          </section>
          {selectedSong && selectedBand && <section className="bands-panel" aria-label={`${selectedSong.title} 대표 미디어`}>
            <FeaturedMediaEditor key={`${identity}:${selectedSong.id}`} song={selectedSong} band={selectedBand} api={api}
              managementOnly onError={(message) => { if (mountedRef.current) setMutationError(message); }} onSaved={() => { if (mountedRef.current) setMutationError(''); }} />
          </section>}
        </>}
  </main>;
};

export const RepresentativeMediaManagementRoute = () => {
  const { bandSlug = '' } = useParams();
  const { user, authGeneration = 0 } = useAuth();
  const identity = `${user?.id || 'anonymous'}:${authGeneration}:${bandSlug}`;
  return <RepresentativeMediaManagement key={identity} bandSlug={bandSlug} identity={identity} />;
};
