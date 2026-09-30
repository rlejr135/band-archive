import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { createSubmissionGuard } from '../../services/authSubmission.js';
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

export const InviteCodeEntry = () => {
  const api = useApi();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const guard = useRef(createSubmissionGuard()).current;
  const submit = async (event) => {
    event.preventDefault();
    if (!guard.tryAcquire()) return;
    const submitted = code;
    setBusy(true); setNotice('');
    // Deliberately clear before the request: invite capabilities must never
    // be retained in component state after a submission completes.
    setCode('');
    try {
      await api.requestJson('/api/v2/invite-codes/join', requestOptions('POST', { code: submitted }));
      setNotice('초대 코드로 밴드에 가입했습니다. 내 밴드 목록을 새로고침해 주세요.');
    } catch {
      // Invalid, expired, and revoked are intentionally indistinguishable.
      setNotice('초대 코드를 사용할 수 없습니다.');
    } finally {
      guard.release(); setBusy(false);
    }
  };
  return <section className="bands-panel" aria-labelledby="invite-entry-title">
    <h3 id="invite-entry-title">초대 코드로 가입</h3>
    <form onSubmit={submit} className="bands-form" aria-busy={busy}>
      <label>초대 코드<input aria-label="초대 코드" value={code} autoComplete="off" spellCheck="false" onChange={(event) => setCode(event.target.value)} /></label>
      <button type="submit" className="secondary-btn" disabled={busy || !code.trim()}>{busy ? '가입 중…' : '가입'}</button>
    </form>
    {notice && <p role="status">{notice}</p>}
  </section>;
};

export const DiscoverBandsPage = () => {
  const { user } = useAuth();
  const api = useApi();
  const authenticated = Boolean(user);
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const [state, setState] = useState({ loading: true, bands: [], pagination: { has_more: false, total: 0 } });
  const [own, setOwn] = useState({ active: new Set(), pending: new Set() });
  const [joining, setJoining] = useState(new Set());
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

  useEffect(() => {
    let current = true;
    if (!authenticated) { setOwn({ active: new Set(), pending: new Set() }); return () => { current = false; }; }
    api.requestJson('/api/v2/bands').then((data) => {
      if (!current) return;
      setOwn({
        active: new Set((data?.bands || []).map((band) => Number(band.id))),
        pending: new Set((data?.pending_memberships || []).map((membership) => Number(membership.band?.id))),
      });
    }).catch(() => { if (current) setOwn({ active: new Set(), pending: new Set() }); });
    return () => { current = false; };
  }, [api, authenticated, user?.id]);

  const join = async (band) => {
    if (!user) { navigate(`/login?returnTo=${encodeURIComponent('/bands/discover')}`); return; }
    const bandId = Number(band.id);
    if (joining.has(bandId) || own.pending.has(bandId) || own.active.has(bandId)) return;
    setJoining((previous) => new Set(previous).add(bandId)); setError('');
    try {
      const result = await api.requestJson(`/api/v2/bands/${bandId}/join-requests`, requestOptions('POST', {}));
      if (result?.created || result?.membership) setOwn((previous) => ({ ...previous, pending: new Set(previous.pending).add(bandId) }));
    } catch { setError(genericFailure); }
    finally { setJoining((previous) => { const next = new Set(previous); next.delete(bandId); return next; }); }
  };

  const submitSearch = (event) => { event.preventDefault(); setPage(0); setQuery(event.currentTarget.elements.search.value.trim()); };
  return <main className="bands-page" aria-labelledby="discover-bands-title">
    <Link to="/bands">← 내 밴드</Link><h2 id="discover-bands-title">공개 밴드 찾기</h2>
    <form onSubmit={submitSearch} className="bands-form"><label>밴드 검색<input name="search" aria-label="밴드 검색" defaultValue={query} /></label><button type="submit" className="secondary-btn">검색</button></form>
    {error && <p role="alert" className="auth-error">{error}</p>}
    {state.loading ? <p aria-live="polite">공개 밴드를 불러오는 중입니다…</p> : <>
      {state.bands.length === 0 ? <p className="bands-help">조건에 맞는 공개 밴드가 없습니다.</p> : <ul className="band-list">{state.bands.map((band) => {
        const id = Number(band.id); const active = own.active.has(id); const pending = own.pending.has(id);
        return <li key={id}><div><strong>{band.name}</strong><p className="bands-help">{band.description || '공개된 밴드 공간입니다.'}</p></div><div className="band-actions"><button type="button" className="secondary-btn" onClick={() => navigate(safeBandPath(band.slug))}>공개 공간 보기</button>{active ? <button type="button" className="secondary-btn" onClick={() => navigate(safeBandPath(band.slug))}>작업 공간 열기</button> : <button type="button" className="secondary-btn" disabled={pending || joining.has(id)} onClick={() => join(band)}>{pending ? '승인 대기 중' : joining.has(id) ? '요청 중…' : user ? '가입 요청' : '로그인 후 가입 요청'}</button>}</div></li>;
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
  return <main className="bands-page" aria-labelledby="create-band-title"><Link to="/bands">← 내 밴드</Link><h2 id="create-band-title">밴드 생성 요청</h2><p className="bands-help">플랫폼 검토 후 승인됩니다.</p>
    <form onSubmit={submit} className="bands-form" aria-busy={busy}><label>밴드 이름<input name="name" required maxLength="120" /></label><label>주소(slug)<input name="slug" required maxLength="80" pattern="[a-z0-9]+(-[a-z0-9]+)*" autoCapitalize="none" /></label><label>소개<textarea name="description" maxLength="2000" /></label><button type="submit" className="primary-btn" disabled={busy}>{busy ? '요청 중…' : '생성 요청 보내기'}</button></form>
    {result && <p role="status">“{result.name}” 생성 요청이 {result.status === 'pending' ? '승인 대기 중' : result.status}입니다.</p>}{error && <p role="alert" className="auth-error">{error}</p>}
  </main>;
};

export const BandManageRoute = () => {
  const { user } = useAuth();
  const { bandSlug } = useParams();
  const identity = `${user?.id || 'visitor'}:${bandSlug || ''}`;
  // A key is intentional: it removes privileged roster/invite/raw-code state
  // synchronously before a different account or band can paint.
  return <BandManagePage key={identity} identity={identity} />;
};

export const BandManagePage = ({ identity: suppliedIdentity }) => {
  const { user } = useAuth();
  const { bandSlug } = useParams(); const api = useApi();
  const identity = suppliedIdentity || `${user?.id || 'visitor'}:${bandSlug || ''}`;
  const [state, setState] = useState({ loading: true, inaccessible: false, band: null, memberships: [], invites: [] });
  const [error, setError] = useState(''); const [rawInvite, setRawInvite] = useState(null);
  const current = useRef({ identity: null, generation: 0, controller: null });
  const isCurrent = useCallback((operation) => current.current.identity === operation.identity && current.current.generation === operation.generation && !operation.controller.signal.aborted, []);
  const load = useCallback(async (operation, { preserveRawInvite = false } = {}) => {
    if (!isCurrent(operation)) return;
    setState({ loading: true, inaccessible: false, band: null, memberships: [], invites: [] }); setError('');
    if (!preserveRawInvite) setRawInvite(null);
    try {
      const detail = await api.requestJson(`/api/v2/bands/by-slug/${encodeURIComponent(bandSlug)}`, { signal: operation.controller.signal });
      const band = detail?.band;
      if (!band?.permissions?.can_manage_members || !band?.permissions?.can_manage_invites) throw new Error('inaccessible');
      const [members, invites] = await Promise.all([
        api.requestJson(`/api/v2/bands/${Number(band.id)}/memberships`, { signal: operation.controller.signal }),
        api.requestJson(`/api/v2/bands/${Number(band.id)}/invite-codes`, { signal: operation.controller.signal }),
      ]);
      if (!isCurrent(operation)) return;
      setState({ loading: false, inaccessible: false, band, memberships: Array.isArray(members?.memberships) ? members.memberships : [], invites: Array.isArray(invites?.invite_codes) ? invites.invite_codes : [] });
    } catch {
      if (isCurrent(operation)) setState({ loading: false, inaccessible: true, band: null, memberships: [], invites: [] });
    }
  }, [api, bandSlug, isCurrent]);
  useEffect(() => {
    const previous = current.current.controller;
    previous?.abort();
    const operation = { identity, generation: current.current.generation + 1, controller: new AbortController() };
    current.current = operation;
    load(operation);
    return () => operation.controller.abort();
  }, [identity, load]);
  const refresh = async (operation, preserveRawInvite = false) => {
    if (!isCurrent(operation)) return;
    await load(operation, { preserveRawInvite });
  };
  const mutate = async (path, body) => {
    const operation = current.current;
    try { await api.requestJson(path, requestOptions('POST', body, operation.controller.signal)); if (isCurrent(operation)) await refresh(operation); }
    catch { if (isCurrent(operation)) setError(genericFailure); }
  };
  const decide = async (membership, approve) => {
    if (!approve && !window.confirm('이 가입 요청을 거절할까요?')) return;
    const operation = current.current;
    try { await api.requestJson(`/api/v2/memberships/${Number(membership.id)}`, requestOptions('PATCH', { action: approve ? 'approve' : 'reject' }, operation.controller.signal)); if (isCurrent(operation)) await refresh(operation); } catch { if (isCurrent(operation)) setError(genericFailure); }
  };
  const createInvite = async () => {
    const operation = current.current; setRawInvite(null);
    try { const data = await api.requestJson(`/api/v2/bands/${Number(state.band.id)}/invite-codes`, requestOptions('POST', {}, operation.controller.signal)); if (isCurrent(operation)) { setRawInvite(typeof data?.code === 'string' ? { identity: operation.identity, code: data.code } : null); await refresh(operation, true); } } catch { if (isCurrent(operation)) setError(genericFailure); }
  };
  const revoke = async (invite) => { if (!window.confirm('이 초대 코드를 취소할까요?')) return; await mutate(`/api/v2/bands/${Number(state.band.id)}/invite-codes/${Number(invite.id)}/revoke`, {}); };
  if (state.loading) return <main className="bands-page"><p aria-live="polite">관리 정보를 불러오는 중입니다…</p></main>;
  if (state.inaccessible) return <main className="bands-page" role="alert"><h2>찾을 수 없는 관리 공간입니다.</h2><p>이 밴드를 관리할 권한이 없습니다.</p><Link to="/bands">내 밴드로 이동</Link></main>;
  const isOwner = state.band?.membership?.role === 'owner'; const pending = state.memberships.filter((item) => item.status === 'pending'); const active = state.memberships.filter((item) => item.status === 'active');
  return <main className="bands-page" aria-labelledby="manage-band-title"><Link to={safeBandPath(state.band.slug)}>← 밴드 공간</Link><h2 id="manage-band-title">{state.band.name} 관리</h2>{error && <p className="auth-error" role="alert">{error}</p>}
    <section className="bands-panel" aria-labelledby="pending-title"><h3 id="pending-title">가입 요청</h3>{pending.length === 0 ? <p className="bands-help">대기 중인 요청이 없습니다.</p> : <ul className="band-list">{pending.map((item) => <li key={item.id}><span>{item.user?.display_name || '새 멤버'}</span><div className="band-actions"><button type="button" className="secondary-btn" onClick={() => decide(item, true)}>승인</button><button type="button" className="secondary-btn" onClick={() => decide(item, false)}>거절</button></div></li>)}</ul>}</section>
    <section className="bands-panel" aria-labelledby="invite-title"><h3 id="invite-title">초대 코드</h3><button type="button" className="secondary-btn" onClick={createInvite}>새 초대 코드 만들기</button>{rawInvite?.identity === identity && <div className="invite-once" role="status"><strong>지금 한 번만 표시되는 코드</strong><code>{rawInvite.code}</code><p>안전한 방법으로 전달한 뒤 이 화면을 벗어나면 다시 확인할 수 없습니다.</p></div>}<ul className="band-list">{state.invites.map((invite) => <li key={invite.id}><span>{invite.revoked_at ? '취소됨' : '사용 가능'} · {invite.created_at}</span>{!invite.revoked_at && <button type="button" className="secondary-btn" onClick={() => revoke(invite)}>취소</button>}</li>)}</ul></section>
    {isOwner && <section className="bands-panel" aria-labelledby="roles-title"><h3 id="roles-title">멤버 역할</h3><ul className="band-list">{active.map((item) => <li key={item.id}><span>{item.user?.display_name || '멤버'} · {item.role}</span>{item.role !== 'owner' && <select aria-label={`${item.user?.display_name || '멤버'} 역할`} value={item.role} onChange={(event) => { const operation = current.current; api.requestJson(`/api/v2/memberships/${Number(item.id)}`, requestOptions('PATCH', { role: event.target.value }, operation.controller.signal)).then(() => isCurrent(operation) && refresh(operation)).catch(() => isCurrent(operation) && setError(genericFailure)); }}><option value="member">member</option><option value="admin">admin</option></select>}</li>)}</ul></section>}
  </main>;
};
