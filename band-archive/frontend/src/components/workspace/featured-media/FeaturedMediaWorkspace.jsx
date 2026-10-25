import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import MediaPlayer from '../../common/MediaPlayer.jsx';
import { ErrorState } from '../WorkspaceFeedback.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { genericError, PAGE_SIZE, requestOptions } from '../workspaceReadSupport.js';
import { takeOneStaleRefresh } from '../workspaceRouteState.js';
import { FeaturedMediaEditor } from './FeaturedMediaEditor.jsx';
import { formatRepresentativeDate, mediaPlayerFile, mergeUniqueEntries } from './featuredMediaPresentation.js';

export const RepresentativeMediaWorkspace = () => {
  const { band, api, workspaceKey } = useWorkspace();
  const [featured, setFeatured] = useState({ loading: true, error: '', media: [], pagination: null });
  const [songs, setSongs] = useState({ loading: true, error: '', entries: [], pagination: null });
  const [selectedSongId, setSelectedSongId] = useState('');
  const [mutationError, setMutationError] = useState('');
  const [busy, setBusy] = useState(false);
  const featuredVersion = useRef(0);
  const songVersion = useRef(0);
  const featuredController = useRef(null);
  const songController = useRef(null);
  const featuredOffsets = useRef(new Map());
  const featuredStaleAttempts = useRef(new Set());

  const loadFeatured = useCallback(async ({ offset = 0, append = false } = {}) => {
    featuredController.current?.abort();
    const controller = new AbortController();
    featuredController.current = controller;
    const version = ++featuredVersion.current;
    setFeatured((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const response = await api.requestJson(bandWorkspacePaths.featuredMedia(band.id, { limit: 25, offset }), requestOptions(controller.signal));
      if (version !== featuredVersion.current || controller.signal.aborted) return;
      const page = Array.isArray(response?.media) ? response.media : [];
      page.forEach((item) => featuredOffsets.current.set(String(item.id), offset));
      setFeatured((previous) => ({ loading: false, error: '', media: append ? mergeUniqueEntries(previous.media, page) : page, pagination: response?.pagination ?? null }));
    } catch {
      if (version === featuredVersion.current && !controller.signal.aborted) setFeatured((previous) => ({ ...previous, loading: false, error: genericError }));
    } finally {
      if (featuredController.current === controller) featuredController.current = null;
    }
  }, [api, band.id]);

  const loadSongs = useCallback(async ({ offset = 0, append = false } = {}) => {
    songController.current?.abort();
    const controller = new AbortController();
    songController.current = controller;
    const version = ++songVersion.current;
    setSongs((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const response = await api.requestJson(bandWorkspacePaths.songs(band.id, { limit: PAGE_SIZE, offset }), requestOptions(controller.signal));
      if (version !== songVersion.current || controller.signal.aborted) return;
      const page = Array.isArray(response?.songs) ? response.songs : [];
      setSongs((previous) => ({ loading: false, error: '', entries: append ? mergeUniqueEntries(previous.entries, page) : page, pagination: response?.pagination ?? null }));
    } catch {
      if (version === songVersion.current && !controller.signal.aborted) setSongs((previous) => ({ ...previous, loading: false, error: genericError }));
    } finally {
      if (songController.current === controller) songController.current = null;
    }
  }, [api, band.id]);

  useEffect(() => {
    featuredVersion.current += 1;
    songVersion.current += 1;
    loadFeatured();
    loadSongs();
    return () => {
      featuredVersion.current += 1; songVersion.current += 1;
      featuredController.current?.abort(); songController.current?.abort();
      featuredController.current = null; songController.current = null;
    };
  }, [loadFeatured, loadSongs, workspaceKey]);

  const canFeatureSongs = songs.entries.filter((song) => song.can_feature === true);
  const unsetFeatured = async (item) => {
    if (!item?.can_feature || !item.song_summary?.id || busy) return;
    setBusy(true); setMutationError('');
    try {
      await api.requestJson(bandWorkspacePaths.songFeaturedMedia(band.id, item.song_summary.id), { method: 'DELETE', ...requestOptions() });
      await loadFeatured();
    } catch (error) {
      setMutationError(error?.status === 403 ? '이 대표 미디어를 변경할 권한이 없습니다.' : '대표 미디어를 해제하지 못했습니다. 다시 시도해 주세요.');
    } finally { setBusy(false); }
  };

  const refreshFeaturedPlayback = (item) => {
    const offset = featuredOffsets.current.get(String(item.id)) ?? 0;
    const currentUrl = item.playback?.video_url || item.playback?.audio_url || item.playback?.url || '';
    if (!takeOneStaleRefresh(featuredStaleAttempts.current, `${offset}:${currentUrl}`, item.id)) return;
    loadFeatured({ offset, append: true });
  };

  return <section className="workspace-page representative-media-page" aria-labelledby="representative-media-title">
    <div className="workspace-page-heading"><div><h1 id="representative-media-title">대표 미디어</h1><p>곡별로 선택된 대표 영상 또는 음원을 확인하고 재생할 수 있습니다.</p></div>{canFeatureSongs.length > 0 && <Link className="secondary-btn" to={`/bands/${encodeURIComponent(band.slug)}/manage/representative-media`}>대표 미디어 관리</Link>}</div>
    {mutationError && <p role="alert" className="workspace-error">{mutationError}</p>}
    {featured.loading && featured.media.length === 0 ? <p aria-live="polite">대표 미디어를 불러오는 중입니다…</p> : featured.error && featured.media.length === 0 ? <ErrorState message={featured.error} retry={() => loadFeatured()} /> : featured.media.length === 0 ? <div className="workspace-empty"><p>등록된 대표 미디어가 없습니다.</p></div> : <div className="representative-media-grid">{featured.media.map((item) => <RepresentativeMediaCard key={item.id} item={item} busy={busy} loading={featured.loading} onUnset={unsetFeatured} onPlaybackError={refreshFeaturedPlayback} />)}</div>}
    {featured.pagination?.has_more && <button className="secondary-btn" disabled={featured.loading} onClick={() => loadFeatured({ offset: featured.pagination?.next_offset ?? featured.media.length, append: true })}>{featured.loading ? '불러오는 중…' : '대표 미디어 더 불러오기'}</button>}
    {featured.error && featured.media.length > 0 && <ErrorState message={featured.error} retry={() => loadFeatured()} />}
    {songs.loading && songs.entries.length === 0 ? <p aria-live="polite">곡별 변경 권한을 확인하는 중입니다…</p> : songs.error && songs.entries.length === 0 ? <ErrorState message={songs.error} retry={() => loadSongs()} /> : canFeatureSongs.length > 0 ? <section className="representative-media-editor" aria-labelledby="representative-media-edit-title">
      <h2 id="representative-media-edit-title">대표 미디어 선택</h2>
      <p>곡을 선택한 뒤, 해당 곡에 등록된 영상이나 음원을 대표로 지정할 수 있습니다.</p>
      <label htmlFor="representative-song-select">곡 선택</label>
      <select id="representative-song-select" value={selectedSongId} onChange={(event) => { setMutationError(''); setSelectedSongId(event.target.value); }}>
        <option value="">곡 선택…</option>
        {canFeatureSongs.map((song) => <option key={song.id} value={song.id}>{song.title} · {song.artist}</option>)}
      </select>
      {songs.pagination?.has_more && <button type="button" className="secondary-btn" disabled={songs.loading} onClick={() => loadSongs({ offset: songs.pagination?.next_offset ?? songs.entries.length, append: true })}>{songs.loading ? '곡을 불러오는 중…' : '곡 더 불러오기'}</button>}
      {selectedSongId && <FeaturedMediaEditor key={`${workspaceKey}:${selectedSongId}`} song={canFeatureSongs.find((song) => String(song.id) === selectedSongId)} band={band} api={api} onSaved={async () => { await loadFeatured(); setMutationError(''); }} onError={setMutationError} />}
    </section> : !songs.loading && <p className="workspace-muted">대표 미디어를 지정할 수 있는 곡이 없습니다. 밴드 소유자 또는 플랫폼 superadmin만 지정할 수 있습니다.</p>}
  </section>;
};

const RepresentativeMediaCard = ({ item, busy, loading, onUnset, onPlaybackError }) => {
  const [playing, setPlaying] = useState(false);
  return <article className="workspace-card representative-media-card">
    <header><h2>{item.song_summary?.title || '곡 제목 없음'}</h2><p>{item.song_summary?.artist || '아티스트 정보 없음'}</p></header>
    <p className="representative-media-meta"><span>{item.file_type === 'video' ? '영상' : '음원'}</span><span>{formatRepresentativeDate(item.created_at)}</span></p>
    <p className="representative-media-name">{item.display_name || `${item.file_type === 'video' ? '영상' : '음원'} #${item.id}`}</p>
    <button type="button" className="primary-btn" aria-expanded={playing} onClick={() => setPlaying((current) => !current)}>{playing ? '재생 닫기' : '재생'}</button>
    {playing && <><MediaPlayer file={mediaPlayerFile(item)} preload="none" onPlaybackError={() => onPlaybackError(item)} /><button type="button" className="secondary-btn" disabled={loading} onClick={() => onPlaybackError(item)}>{loading ? '재생 주소 확인 중…' : '재생 주소 새로고침'}</button></>}
    {item.can_feature && <button type="button" className="secondary-btn" disabled={busy} onClick={() => onUnset(item)}>{busy ? '처리 중…' : '대표 미디어 해제'}</button>}
  </article>;
};
