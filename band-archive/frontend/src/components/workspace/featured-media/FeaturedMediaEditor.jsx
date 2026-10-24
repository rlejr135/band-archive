import { useCallback, useEffect, useRef, useState } from 'react';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import MediaPlayer from '../../common/MediaPlayer.jsx';
import { ErrorState } from '../WorkspaceFeedback.jsx';
import { takeOneStaleRefresh } from '../workspaceRouteState.js';
import { formatRepresentativeDate, mediaPlayerFile, mergeUniqueEntries } from './featuredMediaPresentation.js';
import '../BandWorkspace.css';

const PAGE_SIZE = 20;
const requestOptions = (signal) => ({ authenticated: false, optionalAuth: true, signal });
export const FeaturedMediaEditor = ({ song, band, api, onSaved, onError, managementOnly = false }) => {
  const [candidates, setCandidates] = useState({ loading: true, error: '', entries: [], pagination: null });
  const [media, setMedia] = useState({ loading: true, error: '', entries: [], pagination: null });
  const [previewId, setPreviewId] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const requestVersions = useRef({ candidates: 0, media: 0 });
  const requestControllers = useRef({ candidates: null, media: null });
  const mediaOffsets = useRef(new Map());
  const mediaStaleAttempts = useRef(new Set());
  const managementMounted = useRef(false);

  const requestPage = useCallback(async ({ kind, offset = 0, append = false } = {}) => {
    if (managementOnly && !managementMounted.current) return;
    requestControllers.current[kind]?.abort();
    const controller = new AbortController();
    requestControllers.current[kind] = controller;
    const version = ++requestVersions.current[kind];
    const setTarget = kind === 'candidates' ? setCandidates : setMedia;
    const targetKey = kind === 'candidates' ? 'entries' : 'entries';
    const path = kind === 'candidates'
      ? bandWorkspacePaths.featuredMediaCandidates(band.id, song.id, { limit: PAGE_SIZE, offset })
      : bandWorkspacePaths.songMedia(band.id, song.id, { limit: PAGE_SIZE, offset });
    setTarget((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const response = await api.requestJson(path, requestOptions(controller.signal));
      if (version !== requestVersions.current[kind] || controller.signal.aborted) return;
      const entries = Array.isArray(response?.media) ? response.media : [];
      if (kind === 'media') entries.forEach((entry) => mediaOffsets.current.set(String(entry.id), offset));
      setTarget((previous) => ({ loading: false, error: '', entries: append ? mergeUniqueEntries(previous[targetKey], entries) : entries, pagination: response?.pagination ?? null }));
    } catch (error) {
      if (version === requestVersions.current[kind] && !controller.signal.aborted) {
        const message = error?.status === 403
          ? (kind === 'candidates' ? '대표 미디어를 지정할 권한이 없습니다.' : '이 미디어의 재생 정보를 볼 권한이 없습니다.')
          : (kind === 'candidates' ? '대표 미디어 후보를 불러오지 못했습니다.' : '재생 미디어 정보를 불러오지 못했습니다.');
        setTarget((previous) => ({ ...previous, loading: false, error: message }));
      }
    } finally {
      if (requestControllers.current[kind] === controller) requestControllers.current[kind] = null;
    }
  }, [api, band.id, song.id, managementOnly]);

  useEffect(() => {
    const activeVersions = requestVersions.current;
    const activeControllers = requestControllers.current;
    managementMounted.current = true;
    requestPage({ kind: 'candidates' });
    if (!managementOnly) requestPage({ kind: 'media' });
    return () => {
      managementMounted.current = false;
      activeVersions.candidates += 1; activeVersions.media += 1;
      activeControllers.candidates?.abort(); activeControllers.media?.abort();
      activeControllers.candidates = null; activeControllers.media = null;
    };
  }, [managementOnly, requestPage]);

  const mediaById = new Map(media.entries.map((entry) => [String(entry.id), entry]));
  const choose = async (candidate) => {
    if ((managementOnly && !managementMounted.current) || busyId || candidate.can_feature !== true) return;
    setBusyId(candidate.id); onError('');
    try {
      await api.requestJson(bandWorkspacePaths.songFeaturedMedia(band.id, song.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ media_id: Number(candidate.id) }) });
      if (managementOnly && !managementMounted.current) return;
      await requestPage({ kind: 'candidates' });
      if (managementOnly && !managementMounted.current) return;
      await onSaved();
    } catch (error) {
      if (managementOnly && !managementMounted.current) return;
      onError(error?.status === 403 ? '대표 미디어를 지정할 권한이 없습니다.' : '대표 미디어를 지정하지 못했습니다. 다시 시도해 주세요.');
    } finally { if (!managementOnly || managementMounted.current) setBusyId(null); }
  };

  const unset = async (candidate) => {
    if (!managementOnly || !managementMounted.current || busyId || !candidate?.is_featured) return;
    setBusyId(candidate.id); onError('');
    try {
      await api.requestJson(bandWorkspacePaths.songFeaturedMedia(band.id, song.id), { method: 'DELETE' });
      if (!managementMounted.current) return;
      await requestPage({ kind: 'candidates' });
      if (!managementMounted.current) return;
      await onSaved();
    } catch (error) {
      if (!managementMounted.current) return;
      onError(error?.status === 403 ? '대표 미디어를 해제할 권한이 없습니다.' : '대표 미디어를 해제하지 못했습니다. 다시 시도해 주세요.');
    } finally { if (managementMounted.current) setBusyId(null); }
  };

  const refreshCandidatePlayback = (candidate, currentMedia) => {
    const offset = mediaOffsets.current.get(String(candidate.id)) ?? 0;
    const playback = currentMedia?.playback || {};
    const url = playback.video_url || playback.audio_url || playback.url || '';
    if (!takeOneStaleRefresh(mediaStaleAttempts.current, `${offset}:${url}`, candidate.id)) return;
    requestPage({ kind: 'media', offset });
  };

  return <div className="representative-candidates">
    <h3>{song?.title || '선택한 곡'}의 후보</h3>
    {candidates.loading && candidates.entries.length === 0 ? <p aria-live="polite">후보를 불러오는 중입니다…</p> : candidates.error && candidates.entries.length === 0 ? <ErrorState message={candidates.error} retry={() => requestPage({ kind: 'candidates' })} /> : candidates.entries.length === 0 ? <p className="workspace-muted">대표로 지정할 영상이나 음원이 없습니다.</p> : <div className="representative-candidate-list">{candidates.entries.map((candidate) => {
      const playableMedia = mediaById.get(String(candidate.id));
      return <article className="representative-candidate" key={candidate.id}>
        <div><strong>{candidate.display_name || `${candidate.file_type === 'video' ? '영상' : '음원'} #${candidate.id}`}</strong><span>{candidate.file_type === 'video' ? '영상' : '음원'}{candidate.is_featured ? ' · 현재 대표' : ''}</span><small>{formatRepresentativeDate(candidate.created_at)}</small></div>
        {playableMedia && <button type="button" className="secondary-btn" aria-expanded={previewId === candidate.id} onClick={() => setPreviewId(previewId === candidate.id ? null : candidate.id)}>{previewId === candidate.id ? '미리보기 닫기' : '재생 미리보기'}</button>}
        {managementOnly && candidate.is_featured
          ? <button type="button" className="secondary-btn" disabled={Boolean(busyId)} onClick={() => unset(candidate)}>{busyId === candidate.id ? '해제 중…' : '대표 해제'}</button>
          : <button type="button" className={candidate.is_featured ? 'secondary-btn' : 'primary-btn'} disabled={Boolean(busyId) || candidate.is_featured} onClick={() => choose(candidate)}>{busyId === candidate.id ? '저장 중…' : candidate.is_featured ? '현재 대표 미디어' : '대표로 지정'}</button>}
        {!managementOnly && previewId === candidate.id && playableMedia && <div className="representative-preview"><MediaPlayer file={mediaPlayerFile(playableMedia)} preload="none" onPlaybackError={() => refreshCandidatePlayback(candidate, playableMedia)} /><button type="button" className="secondary-btn" disabled={media.loading} onClick={() => refreshCandidatePlayback(candidate, playableMedia)}>{media.loading ? '재생 주소 확인 중…' : '재생 주소 새로고침'}</button></div>}
        {!managementOnly && previewId === candidate.id && !playableMedia && media.error && <p className="workspace-muted" role="status">{media.error}</p>}
      </article>;
    })}</div>}
    {candidates.pagination?.has_more && <button type="button" className="secondary-btn" disabled={candidates.loading} onClick={() => requestPage({ kind: 'candidates', offset: candidates.pagination?.next_offset ?? candidates.entries.length, append: true })}>{candidates.loading ? '후보를 불러오는 중…' : '후보 더 불러오기'}</button>}
    {candidates.error && candidates.entries.length > 0 && <ErrorState message={candidates.error} retry={() => requestPage({ kind: 'candidates' })} />}
    {!managementOnly && (media.loading && media.entries.length === 0 ? <p aria-live="polite">재생 정보를 확인하는 중입니다…</p> : media.error && media.entries.length === 0 ? <p className="workspace-muted" role="status">{media.error} 후보 지정은 계속할 수 있습니다.</p> : null)}
    {!managementOnly && media.pagination?.has_more && <button type="button" className="secondary-btn" disabled={media.loading} onClick={() => requestPage({ kind: 'media', offset: media.pagination?.next_offset ?? media.entries.length, append: true })}>{media.loading ? '재생 정보를 불러오는 중…' : '재생 정보 더 불러오기'}</button>}
  </div>;
};
