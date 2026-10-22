import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { createSubmissionGuard } from '../../services/authSubmission.js';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import { formatInviteCreatedAt } from '../../services/bandDateTime.js';
import { inviteManagerPath } from '../../services/inviteLinks.js';
import { normalizedJoinName, validJoinName } from '../../services/joinName.js';
import { AnnouncementEditor } from '../workspace/BandWorkspace.jsx';
import { MemberRemovalButton } from './MemberRemoval.jsx';
import { membershipName } from '../../services/memberManagement.js';
import './BandsPage.css';

const requestOptions = (method, body, signal) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
  signal,
});

const useApi = () => {
  const { getAccessToken, refreshSession } = useAuth();
  return useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
};

const safeBandPath = (slug) => `/bands/${encodeURIComponent(slug)}`;
const genericFailure = '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.';

export const InviteLinkGuidance = () => <section className="bands-panel" aria-labelledby="invite-entry-title">
  <h3 id="invite-entry-title">초대 링크로 가입</h3>
  <p>밴드 관리자가 보낸 초대 링크를 열어 밴드 소개를 확인한 뒤 직접 가입할 수 있습니다. 예전 초대 코드는 더 이상 사용할 수 없습니다.</p>
</section>;


export const DiscoverBandsPage = () => {
  const { user, authGeneration = 0 } = useAuth();
  return <DiscoverBandsContent key={`${user?.id || 'visitor'}:${authGeneration}`} />;
};

const DiscoverBandsContent = () => {
  const { user } = useAuth();
  const { activeBands, pendingMemberships, bandsLoaded, reload: reloadMemberships } = useBands();
  const api = useApi();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [state, setState] = useState({ loading: true, bands: [], pagination: { has_more: false, total: 0 } });
  const [joining, setJoining] = useState(new Set());
  const [requestBand, setRequestBand] = useState(null);
  const [name, setName] = useState('');
  const [introduction, setIntroduction] = useState('');
  const joinGuard = useRef(new Set());
  const joinController = useRef(null);
  const current = useRef(true);
  useEffect(() => { current.current = true; return () => { current.current = false; joinController.current?.abort(); }; }, []);
  const [error, setError] = useState('');
  const pageSize = 20;

  useEffect(() => {
    let current = true;
    const load = async () => {
      setState((previous) => ({ ...previous, loading: true })); setError('');
      try {
        const data = await api.requestJson(`/api/v2/public/bands?q=${encodeURIComponent(query)}&limit=${pageSize}&offset=${page * pageSize}`, { authenticated: false });
        if (current) setState({ loading: false, bands: Array.isArray(data?.bands) ? data.bands : [], pagination: data?.pagination || { has_more: false, total: 0 } });
      } catch {
        if (current) { setState((previous) => ({ ...previous, loading: false })); setError('공개 밴드를 불러오지 못했습니다.'); }
      }
    };
    load();
    return () => { current = false; };
  }, [api, page, query]);

  const own = useMemo(() => ({
    active: new Set((activeBands || []).map((band) => Number(band.id))),
    pending: new Set((pendingMemberships || []).map((membership) => Number(membership.band?.id))),
  }), [activeBands, pendingMemberships]);

  const openJoin = (band) => {
    if (!user) { navigate(`/login?returnTo=${encodeURIComponent('/bands/discover')}`); return; }
    setRequestBand(band); setName(''); setIntroduction(''); setError('');
  };
  const join = async (event) => {
    event.preventDefault();
    const band = requestBand;
    if (!band || !user || !validJoinName(name) || Array.from(introduction.trim()).length > 300) return;
    const bandId = Number(band.id);
    if (joinGuard.current.has(bandId) || own.pending.has(bandId) || own.active.has(bandId)) return;
    joinGuard.current.add(bandId);
    const controller = new AbortController(); joinController.current = controller;
    setJoining((previous) => new Set(previous).add(bandId)); setError('');
    try {
      const result = await api.requestJson(`/api/v2/bands/${bandId}/join-requests`, requestOptions('POST', { name: normalizedJoinName(name), introduction: introduction.trim() }, controller.signal));
      if (!current.current) return;
      if (result?.created || result?.membership) { setRequestBand(null); setName(''); setIntroduction(''); await reloadMemberships(); }
    } catch { if (current.current) setError(genericFailure); }
    finally { if (joinController.current === controller) joinController.current = null; joinGuard.current.delete(bandId); if (current.current) setJoining((previous) => { const next = new Set(previous); next.delete(bandId); return next; }); }
  };

  const submitSearch = (event) => { event.preventDefault(); setPage(0); setQuery(event.currentTarget.elements.search.value.trim()); };
  return <main className="bands-page" aria-labelledby="discover-bands-title">
    <Link className="bands-return-link discover-bands-return-link" to="/bands"><span aria-hidden="true">←</span> 내 밴드</Link><h2 id="discover-bands-title">공개 밴드 찾기</h2>
    <form onSubmit={submitSearch} className="bands-form"><label>밴드 검색<input name="search" aria-label="밴드 검색" defaultValue={query} /></label><button type="submit" className="secondary-btn">검색</button></form>
    {error && <p role="alert" className="auth-error">{error}</p>}
    {requestBand && <section className="bands-panel" aria-labelledby="join-introduction-title"><h3 id="join-introduction-title">{requestBand.name} 가입 요청</h3><form className="bands-form" onSubmit={join} aria-busy={joining.has(Number(requestBand.id))}>
      <label htmlFor="join-name">밴드에서 사용할 이름 (필수)<input id="join-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} autoComplete="name" disabled={joining.has(Number(requestBand.id))} aria-describedby="join-name-help" /></label>
      <p id="join-name-help" className="bands-help">승인 후 멤버 목록에 표시할 이름을 100자 이내로 입력해 주세요. 기존에 연결된 멤버 프로필이 있다면 이름은 유지됩니다.</p>
      <label htmlFor="join-introduction">간단한 자기소개 (선택)<textarea id="join-introduction" value={introduction} onChange={(event) => setIntroduction(event.target.value)} disabled={joining.has(Number(requestBand.id))} aria-describedby="join-introduction-help" /></label><p id="join-introduction-help" className="bands-help">밴드 관리자에게 전할 소개를 300자 이내로 적어 주세요. 전화번호·주소 등 민감한 정보는 적지 마세요. 가입 심사에만 사용하며 승인·거절 시 삭제합니다. {Array.from(introduction.trim()).length} / 300자</p><div className="band-actions"><button className="primary-btn" type="submit" disabled={joining.has(Number(requestBand.id)) || !validJoinName(name) || Array.from(introduction.trim()).length > 300}>{joining.has(Number(requestBand.id)) ? '요청 중…' : '가입 요청 보내기'}</button><button className="secondary-btn" type="button" disabled={joining.has(Number(requestBand.id))} onClick={() => { setRequestBand(null); setName(''); setIntroduction(''); }}>취소</button></div></form></section>}
    {state.loading ? <p aria-live="polite">공개 밴드를 불러오는 중입니다…</p> : <>
      {state.bands.length === 0 ? <p className="bands-help">조건에 맞는 공개 밴드가 없습니다.</p> : <ul className="band-list">{state.bands.map((band) => {
        const id = Number(band.id); const active = own.active.has(id); const pending = own.pending.has(id);
        return <li key={id}><div><strong>{band.name}</strong><p className="bands-help">{band.description || '공개된 밴드 공간입니다.'}</p></div><div className="band-actions"><button type="button" className="secondary-btn" onClick={() => navigate(safeBandPath(band.slug))}>공개 공간 보기</button>{active ? <button type="button" className="secondary-btn" onClick={() => navigate(safeBandPath(band.slug))}>작업 공간 열기</button> : <button type="button" className="secondary-btn" disabled={(Boolean(user) && !bandsLoaded) || pending || joining.size > 0} onClick={() => openJoin(band)}>{pending ? '승인 대기 중' : joining.has(id) ? '요청 중…' : user && !bandsLoaded ? '멤버십 확인 중…' : user ? '가입 요청' : '로그인 후 가입 요청'}</button>}</div></li>;
      })}</ul>}
      <nav className="bands-pagination" aria-label="공개 밴드 페이지"><button type="button" className="secondary-btn" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>이전</button><span>{state.pagination.total}개 중 {page + 1}페이지</span><button type="button" className="secondary-btn" disabled={!state.pagination.has_more} onClick={() => setPage((value) => value + 1)}>다음</button></nav>
    </>}
  </main>;
};

export const BandCreationRequestPage = () => {
  const api = useApi(); const guard = useRef(createSubmissionGuard()).current;
  const [busy, setBusy] = useState(false); const [result, setResult] = useState(null); const [error, setError] = useState('');
  const submit = async (event) => {
    event.preventDefault(); if (!guard.tryAcquire()) return;
    const form = new FormData(event.currentTarget); setBusy(true); setError('');
    try { const data = await api.requestJson('/api/v2/band-creation-requests', requestOptions('POST', { name: form.get('name'), slug: form.get('slug'), description: form.get('description') })); setResult(data?.request || null); }
    catch { setError(genericFailure); } finally { guard.release(); setBusy(false); }
  };
  return <main className="bands-page" aria-labelledby="create-band-title">
    <header className="bands-manage-header"><Link className="bands-return-link" to="/bands"><span aria-hidden="true">←</span> 내 밴드</Link><h2 id="create-band-title">밴드 생성 요청</h2></header><p className="bands-help">플랫폼 검토 후 승인됩니다.</p>
    <form onSubmit={submit} className="bands-form" aria-busy={busy}>
      <label>밴드 이름<input name="name" required maxLength="120" /></label>
      <label htmlFor="band-request-slug">밴드 영어명<input id="band-request-slug" name="slug" required maxLength="80" pattern="[a-z0-9]+(-[a-z0-9]+)*" autoCapitalize="none" aria-describedby="band-request-slug-help" /></label>
      <p id="band-request-slug-help" className="bands-help">해당 이름이 밴드 URL 주소가 됩니다. 영문 소문자·숫자로 입력하고, 단어 사이에는 하이픈(-)을 하나씩 사용할 수 있습니다. 공백·대문자는 사용할 수 없으며 최대 80자입니다.</p>
      <label>소개<textarea name="description" maxLength="2000" /></label><button type="submit" className="primary-btn" disabled={busy}>{busy ? '요청 중…' : '생성 요청 보내기'}</button>
    </form>
    {result && <p role="status">“{result.name}” 생성 요청이 {result.status === 'pending' ? '승인 대기 중' : result.status}입니다.</p>}{error && <p role="alert" className="auth-error">{error}</p>}
  </main>;
};

export const BandManageRoute = () => {
  const { user, authGeneration = 0 } = useAuth();
  const { membershipRevisions = {} } = useBands();
  const { bandSlug } = useParams();
  const identity = `${user?.id || 'visitor'}:${authGeneration}:${bandSlug || ''}:${membershipRevisions[bandSlug] || 0}`;
  // A key is intentional: it removes privileged roster/invite/raw-code state
  // synchronously before a different account or band can paint.
  return <BandManagePage key={identity} identity={identity} />;
};

const MANAGEMENT_PAGE_SIZE = 20;
const uniqueGalleryItems = (existing, incoming) => {
  const next = [...existing]; const positions = new Map(next.map((item, index) => [String(item.id), index]));
  for (const item of incoming) {
    const id = String(item.id);
    if (positions.has(id)) next[positions.get(id)] = item;
    else { positions.set(id, next.length); next.push(item); }
  }
  return next;
};

const FeaturedGalleryManager = ({ band, api, workspaceKey, onForbidden }) => {
  const [state, setState] = useState({ loading: true, error: '', items: [], pagination: null, featured: null, busyId: null });
  const sequence = useRef(0); const mutationGeneration = useRef(0); const controllers = useRef(new Set()); const readControllers = useRef(new Set()); const mutationController = useRef(null); const busyId = useRef(null); const imageRefreshes = useRef(new Set()); const [failedUrls, setFailedUrls] = useState({});
  const loadPage = useCallback(async ({ offset = 0, append = false, refreshFeatured = offset === 0 } = {}) => {
    if (busyId.current != null) return;
    const token = ++sequence.current; const controller = new AbortController(); controllers.current.add(controller); readControllers.current.add(controller);
    if (append) setState((previous) => ({ ...previous, loading: true, error: '' }));
    else setState((previous) => ({ ...previous, loading: true, error: '' }));
    try {
      const galleryRequest = api.requestJson(bandWorkspacePaths.gallery(band.id, { limit: MANAGEMENT_PAGE_SIZE, offset }), { signal: controller.signal });
      const featuredRequest = refreshFeatured
        ? api.requestJson(bandWorkspacePaths.featuredGallery(band.id), { signal: controller.signal }).catch((error) => {
          // No configured or currently visible featured image is a normal state.
          if (error?.status === 404) return { gallery: null };
          throw error;
        })
        : Promise.resolve(null);
      const [galleryResponse, featuredResponse] = await Promise.all([galleryRequest, featuredRequest]);
      if (controller.signal.aborted || token !== sequence.current) return;
      const pageItems = Array.isArray(galleryResponse?.gallery) ? galleryResponse.gallery : [];
      const featured = refreshFeatured ? featuredResponse?.gallery ?? null : undefined;
      const items = refreshFeatured ? pageItems.map((item) => ({ ...item, is_featured: String(featured?.id) === String(item.id) })) : pageItems;
      if (refreshFeatured) setFailedUrls({});
      setState((previous) => ({
        ...previous, loading: false, error: '',
        items: append ? uniqueGalleryItems(previous.items, items) : items,
        pagination: galleryResponse?.pagination ?? null,
        ...(refreshFeatured ? { featured } : {}),
      }));
      setFailedUrls((previous) => {
        const next = { ...previous };
        Object.keys(next).forEach((key) => { const current = items.find((item) => String(item.id) === key); if (current?.url && current.url !== next[key]) next[key] = ''; });
        return next;
      });
    } catch (error) {
      if (controller.signal.aborted || token !== sequence.current) return;
      if ([401, 403, 404].includes(error?.status)) onForbidden();
      else setState((previous) => ({ ...previous, loading: false, error: '갤러리를 불러오지 못했습니다.' }));
    } finally { controllers.current.delete(controller); readControllers.current.delete(controller); }
  }, [api, band.id, onForbidden]);
  useEffect(() => {
    let active = true;
    imageRefreshes.current.clear();
    Promise.resolve().then(() => { if (active) loadPage({ offset: 0, refreshFeatured: true }); });
    const activeControllers = controllers.current;
    return () => { active = false; sequence.current += 1; activeControllers.forEach((controller) => controller.abort()); activeControllers.clear(); };
  }, [loadPage, workspaceKey]);
  const toggleFeatured = async (item) => {
    if (busyId.current != null) return;
    const id = Number(item.id); if (!Number.isSafeInteger(id) || id <= 0) return;
    busyId.current = id; sequence.current += 1; mutationGeneration.current += 1; readControllers.current.forEach((controller) => controller.abort()); readControllers.current.clear();
    const controller = new AbortController(); mutationController.current = controller; controllers.current.add(controller); setState((previous) => ({ ...previous, loading: false, busyId: id, error: '' }));
    try {
      const response = await api.requestJson(bandWorkspacePaths.galleryFeatured(band.id, id), requestOptions('PATCH', { is_featured: String(state.featured?.id) !== String(id) }, controller.signal));
      const updated = response?.gallery; if (!updated || Number(updated.id) !== id) throw new Error('Invalid featured gallery response');
      if (controller.signal.aborted) return;
      const selected = updated.is_featured === true;
      setState((previous) => ({
        ...previous, busyId: null,
        featured: selected ? updated : null,
        items: previous.items.map((entry) => String(entry.id) === String(id)
          ? { ...entry, ...updated, is_featured: selected }
          : { ...entry, is_featured: false }),
      }));
    } catch (error) {
      if (!controller.signal.aborted) {
        if (error?.status === 403) onForbidden();
        else setState((previous) => ({ ...previous, busyId: null, error: '대표 사진을 변경하지 못했습니다. 다시 시도해 주세요.' }));
      }
    } finally { controllers.current.delete(controller); busyId.current = null; if (mutationController.current === controller) mutationController.current = null; }
  };
  const refreshImage = async (item, index) => {
    const key = String(item.id); const identity = `${workspaceKey}:${key}`;
    setFailedUrls((previous) => ({ ...previous, [key]: item.url || '' }));
    if (imageRefreshes.current.has(identity)) return;
    imageRefreshes.current.add(identity);
    if (index < 0) {
      const featureGeneration = mutationGeneration.current; const controller = new AbortController(); controllers.current.add(controller); readControllers.current.add(controller);
      try {
        const response = await api.requestJson(bandWorkspacePaths.featuredGallery(band.id), { signal: controller.signal });
        if (!controller.signal.aborted && featureGeneration === mutationGeneration.current && response?.gallery && String(response.gallery.id) === key) {
          setState((previous) => previous.featured && String(previous.featured.id) === key ? { ...previous, featured: { ...previous.featured, ...response.gallery } } : previous);
          setFailedUrls((previous) => ({ ...previous, [key]: '' }));
        }
      } catch (error) { if (!controller.signal.aborted && error?.status === 403) onForbidden(); }
      finally { controllers.current.delete(controller); readControllers.current.delete(controller); }
      return;
    }
    const featureGeneration = mutationGeneration.current; const offset = Math.floor(index / MANAGEMENT_PAGE_SIZE) * MANAGEMENT_PAGE_SIZE; const controller = new AbortController(); controllers.current.add(controller); readControllers.current.add(controller);
    try {
      const response = await api.requestJson(bandWorkspacePaths.gallery(band.id, { limit: MANAGEMENT_PAGE_SIZE, offset }), { signal: controller.signal });
      if (controller.signal.aborted) return;
      const refreshed = (Array.isArray(response?.gallery) ? response.gallery : []).find((entry) => String(entry.id) === key);
      if (refreshed) {
        const { is_featured: _ignoredFeaturedFlag, ...freshItem } = refreshed;
        setState((previous) => ({ ...previous, items: previous.items.map((entry) => String(entry.id) === key ? { ...entry, ...freshItem } : entry), featured: featureGeneration === mutationGeneration.current && previous.featured && String(previous.featured.id) === key ? { ...previous.featured, ...freshItem } : previous.featured }));
        setFailedUrls((previous) => ({ ...previous, [key]: '' }));
      }
    } catch (error) {
      if (!controller.signal.aborted && error?.status === 403) onForbidden();
    } finally { controllers.current.delete(controller); readControllers.current.delete(controller); }
  };
  return <section className="bands-panel manage-gallery" aria-labelledby="manage-gallery-title">
    <h3 id="manage-gallery-title">대표 사진</h3><p className="bands-help">밴드 갤러리에서 대표 사진을 선택하거나 해제할 수 있습니다.</p>
    {state.featured && <div className="manage-featured-current"><div>{state.featured.url && failedUrls[String(state.featured.id)] !== state.featured.url ? <img src={state.featured.url} alt="현재 대표 사진" onError={() => refreshImage(state.featured, state.items.findIndex((item) => String(item.id) === String(state.featured.id)))} /> : <div className="manage-gallery-placeholder" role="img" aria-label="현재 대표 사진을 표시할 수 없습니다">사진 없음</div>}<span>현재 대표 사진</span></div><button type="button" className="secondary-btn" disabled={state.busyId != null} onClick={() => toggleFeatured(state.featured)}>대표 사진 해제</button></div>}
    {state.loading && state.items.length === 0 ? <p aria-live="polite">갤러리를 불러오는 중입니다…</p> : state.error && state.items.length === 0 ? <div className="account-error" role="alert"><span>{state.error}</span><button className="secondary-btn" type="button" onClick={() => loadPage({ offset: 0, refreshFeatured: true })}>다시 시도</button></div> : state.items.length === 0 ? <p className="bands-help">표시할 사진이 없습니다.</p> : <div className="manage-gallery-grid">{state.items.map((item, index) => {
      const isFeatured = String(state.featured?.id) === String(item.id);
      const url = item.url;
      const failed = url && failedUrls[String(item.id)] === url;
      return <article className="manage-gallery-item" key={item.id}>
        {url && !failed ? <img src={url} alt={item.alt_text || item.filename || '밴드 갤러리 사진'} onError={() => refreshImage(item, index)} /> : <div className="manage-gallery-placeholder" role="img" aria-label="사진을 표시할 수 없습니다">사진 없음</div>}
        <div><strong>{item.filename || `사진 #${item.id}`}</strong><button type="button" className={isFeatured ? 'secondary-btn' : 'primary-btn'} disabled={state.busyId != null} onClick={() => toggleFeatured(item)}>{state.busyId === Number(item.id) ? '변경 중…' : isFeatured ? '대표 사진 해제' : '대표 사진으로 선택'}</button></div>
      </article>;
    })}</div>}
    {state.error && state.items.length > 0 && <p className="auth-error" role="alert">{state.error}</p>}
    {state.pagination?.has_more && <button type="button" className="secondary-btn" disabled={state.loading || state.busyId != null} onClick={() => loadPage({ offset: state.pagination.next_offset ?? state.items.length, append: true, refreshFeatured: false })}>{state.loading ? '사진을 불러오는 중…' : '사진 더 불러오기'}</button>}
  </section>;
};

export const BandManagePage = ({ identity: suppliedIdentity }) => {
  const { user } = useAuth();
  const { reload: reloadBands } = useBands();
  const { bandSlug } = useParams(); const api = useApi();
  const identity = suppliedIdentity || `${user?.id || 'visitor'}:${bandSlug || ''}`;
  const [state, setState] = useState({ loading: true, inaccessible: false, band: null, memberships: [], invites: [], invitePagination: null, announcement: null });
  const [error, setError] = useState(''); const [rawInvite, setRawInvite] = useState(null);
  const [inviteBusy, setInviteBusy] = useState(false); const [revokingInviteId, setRevokingInviteId] = useState(null); const [loadingMoreInvites, setLoadingMoreInvites] = useState(false);
  const [galleryPickerOpen, setGalleryPickerOpen] = useState(false);
  const [profileEditing, setProfileEditing] = useState(false);
  const [profileDraft, setProfileDraft] = useState('');
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileError, setProfileError] = useState('');
  const profileRequestRef = useRef(null);
  const profileBusyRef = useRef(false);
  // Mutations belong to the authenticated management session.  Reads have a
  // separate sequence/controller: a second refetch must supersede a slower
  // first refetch without cancelling the mutation that requested it.
  const sessionRef = useRef({ identity: null, generation: 0, controller: null });
  const loadRef = useRef({ identity: null, sessionGeneration: 0, sequence: 0, controller: null });
  const invitePageRef = useRef({ identity: null, sequence: 0, controller: null });
  const isCurrent = useCallback((operation) => sessionRef.current.identity === operation.identity && sessionRef.current.generation === operation.generation && !operation.controller.signal.aborted, []);
  const isCurrentLoad = useCallback((request) => isCurrent(request.session) && loadRef.current.identity === request.identity && loadRef.current.sessionGeneration === request.session.generation && loadRef.current.sequence === request.sequence && !request.controller.signal.aborted, [isCurrent]);
  const onForbidden = useCallback(() => {
    if (sessionRef.current.identity !== identity) return;
    sessionRef.current.controller?.abort(); loadRef.current.controller?.abort();
    setState({ loading: false, inaccessible: true, band: null, memberships: [], invites: [], invitePagination: null, announcement: null });
    setError(''); setRawInvite(null);
  }, [identity]);
  const load = useCallback(async (operation, { preserveRawInvite = false } = {}) => {
    if (!isCurrent(operation)) return;
    invitePageRef.current.controller?.abort();
    invitePageRef.current = { identity: operation.identity, sequence: invitePageRef.current.sequence + 1, controller: null };
    setLoadingMoreInvites(false);
    loadRef.current.controller?.abort();
    const request = {
      identity: operation.identity,
      session: operation,
      sequence: loadRef.current.sequence + 1,
      controller: new AbortController(),
    };
    loadRef.current = { identity: request.identity, sessionGeneration: operation.generation, sequence: request.sequence, controller: request.controller };
    setState({ loading: true, inaccessible: false, band: null, memberships: [], invites: [], invitePagination: null, announcement: null }); setError('');
    if (!preserveRawInvite) setRawInvite(null);
    try {
      const detail = await api.requestJson(`/api/v2/bands/by-slug/${encodeURIComponent(bandSlug)}`, { signal: request.controller.signal });
      const band = detail?.band;
      if (!band?.permissions?.can_manage_members || !band?.permissions?.can_manage_invites) throw new Error('inaccessible');
      const [members, invites, announcement] = await Promise.all([
        api.requestJson(`/api/v2/bands/${Number(band.id)}/memberships`, { signal: request.controller.signal }),
        api.requestJson(`${inviteManagerPath(band.id)}?limit=20&offset=0`, { signal: request.controller.signal }),
        api.requestJson(bandWorkspacePaths.announcement(band.id), { signal: request.controller.signal }),
      ]);
      if (!isCurrentLoad(request)) return;
      setState({ loading: false, inaccessible: false, band, memberships: Array.isArray(members?.memberships) ? members.memberships : [], invites: Array.isArray(invites?.invite_links) ? invites.invite_links : [], invitePagination: invites?.pagination ?? { limit: 20, offset: 0, has_more: false }, announcement: announcement?.announcement ?? null });
    } catch {
      if (isCurrentLoad(request)) setState({ loading: false, inaccessible: true, band: null, memberships: [], invites: [], invitePagination: null, announcement: null });
    }
  }, [api, bandSlug, isCurrent, isCurrentLoad]);
  useEffect(() => {
    const previous = sessionRef.current.controller;
    previous?.abort();
    const operation = { identity, generation: sessionRef.current.generation + 1, controller: new AbortController() };
    sessionRef.current = operation;
    load(operation);
    return () => { operation.controller.abort(); invitePageRef.current.controller?.abort(); if (loadRef.current.sessionGeneration === operation.generation) loadRef.current.controller?.abort(); };
  }, [identity, load]);
  useEffect(() => () => profileRequestRef.current?.abort(), [identity]);
  const refresh = async (operation, preserveRawInvite = false) => {
    if (!isCurrent(operation)) return;
    await load(operation, { preserveRawInvite });
  };
  const decide = async (membership, approve) => {
    if (!approve && !window.confirm('이 가입 요청을 거절할까요?')) return;
    const operation = sessionRef.current;
    try { await api.requestJson(`/api/v2/memberships/${Number(membership.id)}`, requestOptions('PATCH', { action: approve ? 'approve' : 'reject' }, operation.controller.signal)); if (isCurrent(operation)) await refresh(operation); } catch { if (isCurrent(operation)) setError(genericFailure); }
  };
  const createInvite = async () => {
    if (inviteBusy) return;
    setInviteBusy(true);
    const operation = sessionRef.current; setRawInvite(null);
    try {
      const data = await api.requestJson(inviteManagerPath(state.band.id), requestOptions('POST', {}, operation.controller.signal));
      const created = data?.invite_link;
      if (isCurrent(operation) && typeof data?.token === 'string' && Number.isInteger(created?.id)) {
        const url = new URL('/invite', window.location.origin); url.hash = new URLSearchParams({ token: data.token }).toString();
        setRawInvite({ identity: operation.identity, url: url.toString(), invite: created });
        await refresh(operation, true);
      } else if (isCurrent(operation)) setError('초대 링크를 만들지 못했습니다. 다시 시도해 주세요.');
    } catch { if (isCurrent(operation)) setError(genericFailure); }
    finally { if (isCurrent(operation)) setInviteBusy(false); }
  };
  const revoke = async (invite) => {
    if (inviteBusy || !window.confirm('이 초대 링크를 폐기할까요?')) return;
    setInviteBusy(true); setRevokingInviteId(invite.id);
    const operation = sessionRef.current;
    try {
      const result = await api.requestJson(inviteManagerPath(state.band.id, `/${Number(invite.id)}/revoke`), requestOptions('POST', {}, operation.controller.signal));
      if (isCurrent(operation) && result?.invite_link?.id === invite.id && result.invite_link.revoked_at) {
        setState((previous) => ({ ...previous,
          invites: previous.invites.map((item) => item.id === invite.id ? result.invite_link : item),
        }));
        setRawInvite((previous) => previous?.invite?.id === invite.id ? null : previous);
        setError('');
      } else if (isCurrent(operation)) setError(genericFailure);
    } catch { if (isCurrent(operation)) setError(genericFailure); }
    finally { setInviteBusy(false); setRevokingInviteId(null); }
  };
  const loadMoreInvites = async () => {
    const operation = sessionRef.current; const currentBandId = state.band?.id;
    if (loadingMoreInvites || !state.invitePagination?.has_more || !isCurrent(operation) || !currentBandId) return;
    const controller = new AbortController();
    const sequence = invitePageRef.current.sequence + 1;
    invitePageRef.current.controller?.abort();
    invitePageRef.current = { identity: operation.identity, sequence, controller };
    const abortPage = () => controller.abort();
    operation.controller.signal.addEventListener('abort', abortPage, { once: true });
    setLoadingMoreInvites(true);
    try {
      const offset = state.invites.length;
      const page = await api.requestJson(`${inviteManagerPath(currentBandId)}?limit=20&offset=${offset}`, { signal: controller.signal });
      if (!isCurrent(operation) || controller.signal.aborted || invitePageRef.current.sequence !== sequence || state.band?.id !== currentBandId || !Array.isArray(page?.invite_links)) return;
      setState((previous) => previous.band?.id === currentBandId ? {
        ...previous,
        invites: [...previous.invites, ...page.invite_links.filter((item) => !previous.invites.some((existing) => existing.id === item.id))],
        invitePagination: page.pagination ?? { limit: 20, offset, has_more: false },
      } : previous);
    } catch { if (isCurrent(operation) && !controller.signal.aborted) setError(genericFailure); }
    finally {
      operation.controller.signal.removeEventListener('abort', abortPage);
      if (invitePageRef.current.sequence === sequence) { invitePageRef.current.controller = null; setLoadingMoreInvites(false); }
    }
  };
  const copyInvite = async () => {
    if (rawInvite?.identity !== identity || !rawInvite.url) return;
    try { await navigator.clipboard.writeText(rawInvite.url); setError(''); }
    catch { setError('복사하지 못했습니다. 링크를 직접 선택해 복사해 주세요.'); }
  };
  const announcementSaved = (announcement) => {
    if (sessionRef.current.identity !== identity || sessionRef.current.controller?.signal.aborted) return;
    setState((previous) => previous.band?.id === state.band?.id ? { ...previous, announcement } : previous);
  };
  const saveProfile = async (event) => {
    event.preventDefault();
    const operation = sessionRef.current; const currentBand = state.band;
    if (!currentBand || currentBand.permissions?.can_edit_band_profile !== true || profileBusyRef.current || !isCurrent(operation)) return;
    const description = profileDraft;
    const controller = new AbortController(); profileRequestRef.current = controller;
    profileBusyRef.current = true; setProfileBusy(true); setProfileError('');
    try {
      const response = await api.requestJson(bandWorkspacePaths.bandProfile(currentBand.id), requestOptions('PATCH', { description }, controller.signal));
      const updatedBand = response?.band;
      if (!isCurrent(operation) || controller.signal.aborted || String(updatedBand?.id) !== String(currentBand.id)) return;
      setState((previous) => previous.band && String(previous.band.id) === String(currentBand.id)
        ? { ...previous, band: { ...previous.band, ...updatedBand } }
        : previous);
      setProfileDraft(updatedBand.description || ''); setProfileEditing(false); reloadBands();
    } catch (error) {
      if (!isCurrent(operation) || controller.signal.aborted) return;
      if (error?.status === 403 || error?.status === 404) {
        setProfileEditing(false); setProfileError('소개를 수정할 권한이 변경되었습니다.');
        setState((previous) => previous.band && String(previous.band.id) === String(currentBand.id)
          ? { ...previous, band: { ...previous.band, permissions: { ...previous.band.permissions, can_edit_band_profile: false } } }
          : previous);
        try {
          const detail = await api.requestJson(bandWorkspacePaths.bandBySlug(bandSlug), { signal: operation.controller.signal });
          const refreshedBand = detail?.band;
          if (!isCurrent(operation) || !refreshedBand || String(refreshedBand.id) !== String(currentBand.id)) return;
          if (!refreshedBand.permissions?.can_manage_members || !refreshedBand.permissions?.can_manage_invites) { onForbidden(); return; }
          setState((previous) => previous.band && String(previous.band.id) === String(currentBand.id) ? { ...previous, band: refreshedBand } : previous);
          reloadBands();
        } catch (recheckError) {
          if (isCurrent(operation)) {
            if ([401, 403, 404].includes(recheckError?.status)) onForbidden();
            else {
              setState((previous) => previous.band && String(previous.band.id) === String(currentBand.id)
                ? { ...previous, band: { ...previous.band, permissions: { ...previous.band.permissions, can_edit_band_profile: false } } }
                : previous);
              setProfileError('권한을 다시 확인하지 못했습니다. 밴드 관리 화면을 새로고침해 주세요.');
            }
          }
        }
      } else setProfileError('소개를 저장하지 못했습니다. 입력 내용을 확인하고 다시 시도해 주세요.');
    } finally {
      if (profileRequestRef.current === controller) {
        profileRequestRef.current = null;
        if (isCurrent(operation)) { profileBusyRef.current = false; setProfileBusy(false); }
      }
    }
  };
  if (state.loading) return <main className="bands-page"><p aria-live="polite">관리 정보를 불러오는 중입니다…</p></main>;
  if (state.inaccessible) return <main className="bands-page manage-page" role="alert"><h2>찾을 수 없는 관리 공간입니다.</h2><p>이 밴드를 관리할 권한이 없습니다.</p><Link className="bands-return-link" to="/bands"><span aria-hidden="true">←</span> 내 밴드로 이동</Link></main>;
  const isOwner = state.band?.membership?.role === 'owner'; const pending = state.memberships.filter((item) => item.status === 'pending'); const active = state.memberships.filter((item) => item.status === 'active');
  return <main className="bands-page manage-page" aria-labelledby="manage-band-title"><header className="bands-manage-header"><Link className="bands-return-link" to={safeBandPath(state.band.slug)}><span aria-hidden="true">←</span> 밴드 공간으로 돌아가기</Link><h2 id="manage-band-title">{state.band.name} 관리</h2>{state.band.permissions?.can_manage_logo === true && <Link className="secondary-btn" to={`${safeBandPath(state.band.slug)}/manage/logo`}>로고 관리</Link>}</header>{error && <p className="auth-error" role="alert">{error}</p>}
    <section className="bands-panel" aria-labelledby="manage-profile-title"><h3 id="manage-profile-title">밴드 소개</h3>{profileEditing ? <form className="bands-form" onSubmit={saveProfile}><label htmlFor="band-profile-description">소개<textarea id="band-profile-description" maxLength={2000} value={profileDraft} onChange={(event) => setProfileDraft(event.target.value)} disabled={profileBusy} /></label><p className="bands-help">{profileDraft.length} / 2,000자</p>{profileError && <p className="auth-error" role="alert">{profileError}</p>}<div className="band-actions"><button type="submit" className="primary-btn" disabled={profileBusy}>{profileBusy ? '저장 중…' : '소개 저장'}</button><button type="button" className="secondary-btn" disabled={profileBusy} onClick={() => { setProfileDraft(state.band.description || ''); setProfileEditing(false); setProfileError(''); }}>취소</button></div></form> : <><p>{state.band.description || '소개가 없습니다.'}</p>{profileError && <p className="auth-error" role="alert">{profileError}</p>}{state.band.permissions?.can_edit_band_profile === true && <button type="button" className="secondary-btn" onClick={() => { setProfileDraft(state.band.description || ''); setProfileError(''); setProfileEditing(true); }}>소개 수정</button>}</>}</section>
    <section className="bands-panel" aria-labelledby="manage-announcement-title"><h3 id="manage-announcement-title">밴드 공지</h3><AnnouncementEditor announcement={state.announcement} band={state.band} api={api} workspaceKey={identity} onSaved={announcementSaved} onForbidden={onForbidden} /></section>
    <section className="bands-panel" aria-labelledby="manage-gallery-picker-title"><h3 id="manage-gallery-picker-title">대표 사진</h3>{galleryPickerOpen ? <FeaturedGalleryManager band={state.band} api={api} workspaceKey={identity} onForbidden={onForbidden} /> : <button type="button" className="secondary-btn" onClick={() => setGalleryPickerOpen(true)}>갤러리에서 선택</button>}</section>
    <section className="bands-panel" aria-labelledby="pending-title"><h3 id="pending-title">가입 요청</h3>{pending.length === 0 ? <p className="bands-help">대기 중인 요청이 없습니다.</p> : <ul className="band-list">{pending.map((item) => <li key={item.id}><div><strong>{item.requested_name || item.user?.display_name || item.user?.email || '새 멤버'}</strong><p className="bands-help">요청 시각: <time>{formatInviteCreatedAt(item.requested_at)}</time></p><p className="membership-introduction">{typeof item.introduction === 'string' && item.introduction.trim() ? item.introduction : '작성한 자기소개가 없습니다.'}</p></div><div className="band-actions"><button type="button" className="secondary-btn" onClick={() => decide(item, true)}>승인</button><button type="button" className="secondary-btn" onClick={() => decide(item, false)}>거절</button></div></li>)}</ul>}</section>
    <section className="bands-panel" aria-labelledby="invite-title"><h3 id="invite-title">초대 링크</h3><p className="bands-help">각 링크는 발급 후 24시간 동안 여러 사람이 사용할 수 있으며 언제든 폐기할 수 있습니다. 원문은 발급 직후에만 표시됩니다.</p><button type="button" className="secondary-btn" onClick={createInvite} disabled={inviteBusy}>{inviteBusy ? '처리 중…' : '새 초대 링크 만들기'}</button>{rawInvite?.identity === identity && <div className="invite-once" role="status"><strong>지금 한 번만 표시되는 초대 링크</strong><label htmlFor="invite-link-once">초대 링크</label><input id="invite-link-once" readOnly value={rawInvite.url} onFocus={(event) => event.target.select()} /><button type="button" className="secondary-btn" onClick={copyInvite}>링크 복사</button><p>안전한 방법으로 전달하세요. 이 화면을 벗어나면 다시 확인할 수 없습니다.</p></div>}<ul className="band-list">{state.invites.filter((invite) => !invite.revoked_at).map((invite) => { const expired = invite.expires_at && new Date(invite.expires_at).getTime() <= Date.now(); return <li key={invite.id}><span>{expired ? '만료됨' : '사용 가능'} · 발급 <time>{formatInviteCreatedAt(invite.created_at)}</time>{invite.expires_at && <> · 만료 <time>{formatInviteCreatedAt(invite.expires_at)}</time></>}</span>{!expired && <button type="button" className="secondary-btn" disabled={inviteBusy} onClick={() => revoke(invite)}>{revokingInviteId === invite.id ? '폐기 중…' : '폐기'}</button>}</li>; })}</ul>{state.invitePagination?.has_more && <button type="button" className="secondary-btn" onClick={loadMoreInvites} disabled={loadingMoreInvites}>{loadingMoreInvites ? '불러오는 중…' : '이전 초대 링크 더 보기'}</button>}</section>
    {isOwner && <section className="bands-panel" aria-labelledby="roles-title">
      <h3 id="roles-title">멤버 역할</h3>
      <p className="bands-help">추방 시 밴드 접근 권한만 해제합니다. 작성한 콘텐츠와 멤버 프로필은 보존되며, 추후 다시 가입할 수 있습니다.</p>
      <button type="button" className="secondary-btn" onClick={() => refresh(sessionRef.current)}>명단 새로고침</button>
      <ul className="band-list">{active.map((item) => <li key={item.id}>
        <span>{membershipName(item)} · {item.role}</span>
        <div className="band-actions">
          {item.role !== 'owner' && <select aria-label={`${membershipName(item)} 역할`} value={item.role} onChange={(event) => { const operation = sessionRef.current; api.requestJson(`/api/v2/memberships/${Number(item.id)}`, requestOptions('PATCH', { role: event.target.value }, operation.controller.signal)).then(() => isCurrent(operation) && refresh(operation)).catch(() => isCurrent(operation) && setError(genericFailure)); }}><option value="member">member</option><option value="admin">admin</option></select>}
          <MemberRemovalButton api={api} band={state.band} membership={item} onRemoved={() => { const operation = sessionRef.current; if (!isCurrent(operation)) return; setState((previous) => ({ ...previous, memberships: previous.memberships.filter((entry) => entry.id !== item.id) })); }} />
        </div>
      </li>)}</ul>
    </section>}
  </main>;
};
