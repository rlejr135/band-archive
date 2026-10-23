import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandIntroductionPath } from '../../services/bandIntroductionApi.js';
import { safeExternalHttpUrl } from '../../services/safeExternalUrl.js';
import { BandJoinRequest } from './BandJoinRequest.jsx';
import './BandIntroduction.css';
import './BandsPage.css';

const errorMessage = (error) => {
  if (error?.status === 404) return '이 밴드를 찾을 수 없습니다.';
  if (error?.status === 401) return '로그인 상태를 확인할 수 없습니다. 다시 로그인해 주세요.';
  return '밴드 소개를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.';
};

const isIntroduction = (value, expectedSlug) => {
  const band = value?.band;
  const viewer = value?.viewer;
  const songCount = value?.stats?.total_songs;
  const validStatus = viewer?.membership_status == null || ['active', 'pending', 'left', 'rejected'].includes(viewer.membership_status);
  return Boolean(band
    && typeof band.id === 'number' && Number.isInteger(band.id) && band.id > 0
    && band.slug === expectedSlug && typeof band.name === 'string' && band.name.trim()
    && (band.description == null || typeof band.description === 'string')
    && (value.featured_gallery == null || (typeof value.featured_gallery === 'object' && !Array.isArray(value.featured_gallery)))
    && Number.isInteger(songCount) && songCount >= 0
    && ['PUBLIC', 'MEMBERS'].includes(value.song_count_scope)
    && (value.announcement == null || (typeof value.announcement === 'object' && typeof value.announcement.content === 'string'))
    && typeof viewer?.can_open_workspace === 'boolean' && validStatus);
};

const IntroductionActions = ({ band, viewer }) => <section className="band-introduction-actions" aria-label="밴드 참여 상태">
  {viewer.can_open_workspace
    ? <><p>이미 가입한 밴드입니다.</p><Link className="primary-btn" to={`/bands/${encodeURIComponent(band.slug)}`}>밴드 공간 열기</Link></>
    : viewer.membership_status === 'pending'
      ? <p role="status">가입 요청이 승인 대기 중입니다.</p>
      : <><p>{viewer.membership_status === 'left' || viewer.membership_status === 'rejected' ? '현재 이 밴드의 멤버가 아닙니다.' : '밴드의 곡과 기록은 가입 후 확인할 수 있습니다.'}</p><Link className="secondary-btn" to="/bands/discover">공개 밴드 찾기로 돌아가기</Link></>}
</section>;

export const BandIntroduction = ({ introduction, photoScope = '', onRefreshPhoto, photoRefreshPending = false, failedPhotoKey = '', children }) => {
  const band = introduction.band;
  const photo = introduction.featured_gallery;
  const photoUrl = safeExternalHttpUrl(photo?.url);
  const currentPhotoKey = `${photoScope}:${photo?.id ?? 'missing'}:${photo?.url ?? ''}`;
  const displayPhotoUrl = currentPhotoKey === failedPhotoKey ? null : photoUrl;
  const songCount = Number.isFinite(Number(introduction.stats?.total_songs)) ? Number(introduction.stats.total_songs) : 0;
  const songLabel = introduction.song_count_scope === 'PUBLIC' ? '공개곡' : '전체 곡';
  const announcement = introduction.announcement?.content?.trim();

  return <main className="band-introduction-page" aria-labelledby="band-introduction-title">
    <Link className="bands-return-link" to="/bands/discover"><span aria-hidden="true">←</span> 공개 밴드 찾기</Link>
    <article className="band-introduction-card">
      <div className="band-introduction-photo">
        {displayPhotoUrl ? <img src={displayPhotoUrl} alt={`${band.name} 대표 사진`} onError={onRefreshPhoto} />
          : <div className="band-introduction-photo-fallback" role="img" aria-label={`${band.name} 대표 사진 없음`}>♫</div>}
        {photoRefreshPending && <span className="band-introduction-photo-status" role="status">사진을 다시 불러오는 중…</span>}
      </div>
      <div className="band-introduction-copy">
        <p className="band-introduction-eyebrow">BAND INTRODUCTION</p>
        <h1 id="band-introduction-title">{band.name}</h1>
        <p className="band-introduction-song-count"><strong>{songCount}</strong> {songLabel}</p>
        <section aria-labelledby="band-introduction-description-title">
          <h2 id="band-introduction-description-title">밴드 소개</h2>
          <p className="band-introduction-description">{band.description?.trim() || '등록된 밴드 소개가 없습니다.'}</p>
        </section>
        <section aria-labelledby="band-introduction-announcement-title">
          <h2 id="band-introduction-announcement-title">공지사항</h2>
          <p className="band-introduction-announcement">{announcement || '공개된 공지사항이 없습니다.'}</p>
        </section>
      </div>
    </article>
    {children}
  </main>;
};

export const BandIntroductionRoute = ({ joinMode = false }) => {
  const { bandSlug = '' } = useParams();
  const { user, getAccessToken, refreshSession } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const identity = `${user?.id || 'anonymous'}:${bandSlug}`;
  const [state, setState] = useState({ identity, loading: true, introduction: null, error: '' });
  const [photoRefreshPending, setPhotoRefreshPending] = useState(false);
  const [failedPhotoKey, setFailedPhotoKey] = useState('');
  const requestRef = useRef(null);
  const requestScopeRef = useRef(identity);
  const photoRefreshRef = useRef('');

  const readIntroduction = useCallback(async (signal) => api.requestJson(bandIntroductionPath(bandSlug), {
    authenticated: false,
    optionalAuth: true,
    cache: 'no-store',
    signal,
  }, '밴드 소개를 불러오지 못했습니다.'), [api, bandSlug]);

  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = controller;
    requestScopeRef.current = identity;
    photoRefreshRef.current = '';
    setPhotoRefreshPending(false);
    setFailedPhotoKey('');
    setState({ identity, loading: true, introduction: null, error: '' });
    readIntroduction(controller.signal).then((response) => {
      if (controller.signal.aborted) return;
      const introduction = isIntroduction(response?.introduction, bandSlug) ? response.introduction : null;
      setState({ identity, loading: false, introduction, error: introduction ? '' : '밴드 소개를 불러오지 못했습니다.' });
    }).catch((error) => {
      if (controller.signal.aborted || error?.name === 'AbortError') return;
      setState({ identity, loading: false, introduction: null, error: errorMessage(error) });
    });
    return () => { controller.abort(); if (requestRef.current === controller) requestRef.current = null; };
  }, [bandSlug, identity, readIntroduction]);

  const refreshPhoto = useCallback(async () => {
    const controller = requestRef.current;
    const currentIntroduction = state.identity === identity ? state.introduction : null;
    const currentPhoto = currentIntroduction?.featured_gallery;
    const photoId = currentPhoto?.id ?? 'missing';
    const refreshKey = `${identity}:${photoId}`;
    const failedKey = `${identity}:${photoId}:${currentPhoto?.url ?? ''}`;
    if (!controller || controller.signal.aborted || requestRef.current !== controller || requestScopeRef.current !== identity) {
      setFailedPhotoKey(failedKey);
      return;
    }
    if (photoRefreshRef.current === refreshKey) {
      setFailedPhotoKey(failedKey);
      return;
    }
    photoRefreshRef.current = refreshKey;
    setPhotoRefreshPending(true);
    try {
      const response = await readIntroduction(controller?.signal);
      if (!controller || controller.signal.aborted || requestRef.current !== controller) return;
      const introduction = isIntroduction(response?.introduction, bandSlug) ? response.introduction : null;
      setState((previous) => previous.identity === identity
        ? { ...previous, introduction: introduction || previous.introduction }
        : previous);
      const nextPhoto = introduction?.featured_gallery;
      if (!introduction || (nextPhoto?.id === currentPhoto?.id && nextPhoto?.url === currentPhoto?.url)) setFailedPhotoKey(failedKey);
    } catch {
      if (!controller.signal.aborted && requestRef.current === controller) setFailedPhotoKey(failedKey);
    } finally {
      if (controller && !controller.signal.aborted && requestRef.current === controller) setPhotoRefreshPending(false);
    }
  }, [bandSlug, identity, readIntroduction, state.identity, state.introduction]);

  if (state.identity !== identity || state.loading) return <main className="band-introduction-page" aria-live="polite"><p>밴드 소개를 불러오는 중입니다…</p></main>;
  if (state.error || !state.introduction) return <main className="band-introduction-page" aria-labelledby="band-introduction-error-title"><h1 id="band-introduction-error-title">밴드 소개</h1><p role="alert">{state.error || '밴드 소개를 불러오지 못했습니다.'}</p><Link className="bands-return-link" to="/bands/discover"><span aria-hidden="true">←</span> 공개 밴드 찾기</Link></main>;
  return <BandIntroduction introduction={state.introduction} photoScope={identity} onRefreshPhoto={refreshPhoto} photoRefreshPending={photoRefreshPending} failedPhotoKey={failedPhotoKey}>
    {joinMode
      ? <BandJoinRequest key={`${identity}:${state.introduction.band.id}`} band={state.introduction.band} viewer={state.introduction.viewer} />
      : <IntroductionActions band={state.introduction.band} viewer={state.introduction.viewer} />}
  </BandIntroduction>;
};

