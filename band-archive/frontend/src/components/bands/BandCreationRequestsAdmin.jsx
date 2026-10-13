import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { usePlatformAdmin } from './usePlatformAdmin.js';
import './BandCreationRequestsAdmin.css';

const genericError = '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.';
const messageFor = (error) => error?.status === 401 ? '로그인 세션을 확인할 수 없습니다. 다시 로그인해 주세요.' : error?.status === 403 ? '플랫폼 관리자 권한이 없어 요청을 확인할 수 없습니다.' : genericError;
const requestPath = '/api/v2/platform/band-creation-requests?status=pending';

export const BandCreationRequestsAdminRoute = () => {
  const { user } = useAuth();
  return <BandCreationRequestsAdmin key={user?.id || 'anonymous'} />;
};

export const BandCreationRequestsAdmin = () => {
  const { user, getAccessToken, refreshSession } = useAuth();
  const permission = usePlatformAdmin();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const identity = user?.id || '';
  const [state, setState] = useState({ identity: '', loading: false, requests: [], error: '' });
  const [busyId, setBusyId] = useState(null);
  const [accessDenied, setAccessDenied] = useState(false);
  const busyRef = useRef(false);
  const loadRef = useRef(false);
  const mountedRef = useRef(true);
  const sessionRef = useRef(identity);
  sessionRef.current = identity;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    if (loadRef.current || !permission.admin || accessDenied) return;
    loadRef.current = true;
    const requestedIdentity = identity;
    setState({ identity: requestedIdentity, loading: true, requests: [], error: '' });
    try {
      const data = await api.requestJson(requestPath);
      if (mountedRef.current && sessionRef.current === requestedIdentity) setState({ identity: requestedIdentity, loading: false, requests: Array.isArray(data?.requests) ? data.requests.filter((request) => request?.status === 'pending') : [], error: '' });
    } catch (error) {
      if (mountedRef.current && sessionRef.current === requestedIdentity) {
        setState({ identity: requestedIdentity, loading: false, requests: [], error: messageFor(error) });
        if (error?.status === 401 || error?.status === 403) setAccessDenied(true);
      }
    } finally {
      loadRef.current = false;
    }
  }, [accessDenied, api, identity, permission.admin]);

  useEffect(() => {
    if (!permission.loading && permission.admin) load();
  }, [load, permission.admin, permission.loading]);

  const decide = async (request, action) => {
    if (busyRef.current || !permission.admin || accessDenied || request?.status !== 'pending' || !request?.id) return;
    const label = action === 'approve' ? '승인' : '거절';
    if (!window.confirm(`“${request.name || '이 밴드'}” 생성 요청을 ${label}하시겠습니까?`)) return;
    busyRef.current = true;
    setBusyId(request.id);
    setState((current) => ({ ...current, error: '' }));
    const operationIdentity = identity;
    try {
      await api.requestJson(`/api/v2/platform/band-creation-requests/${encodeURIComponent(request.id)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }),
      });
      if (mountedRef.current && sessionRef.current === operationIdentity) await load();
    } catch (error) {
      if (mountedRef.current && sessionRef.current === operationIdentity) {
        setState((current) => ({ ...current, requests: error?.status === 401 || error?.status === 403 ? [] : current.requests, error: messageFor(error) }));
        if (error?.status === 401 || error?.status === 403) setAccessDenied(true);
      }
    } finally {
      busyRef.current = false;
      if (mountedRef.current && sessionRef.current === operationIdentity) setBusyId(null);
    }
  };

  const visibleState = state.identity === identity ? state : { loading: permission.loading || permission.admin, requests: [], error: '' };
  return <main className="platform-requests" aria-labelledby="platform-requests-title">
    <header className="platform-requests-header"><Link to="/bands">← 내 밴드</Link><span>SUPERADMIN</span></header>
    <h1 id="platform-requests-title">밴드 생성 요청 검토</h1>
    <p className="platform-requests-intro">대기 중인 밴드 생성 요청을 확인하고 처리합니다.</p>
    {permission.loading ? <p role="status" aria-live="polite">관리자 권한을 확인하는 중입니다…</p>
      : permission.error ? <section className="platform-requests-state" role="alert"><p>{messageFor(permission.error)}</p></section>
        : !permission.admin ? <section className="platform-requests-state"><h2>접근할 수 없습니다</h2><p>플랫폼 관리자만 이 페이지를 사용할 수 있습니다.</p><Link to="/bands">내 밴드로 돌아가기</Link></section>
          : accessDenied ? <section className="platform-requests-state" role="alert"><p>{visibleState.error || '플랫폼 관리자 권한이 없어 요청을 확인할 수 없습니다.'}</p><Link to="/bands">내 밴드로 돌아가기</Link></section> : <section aria-label="대기 중인 요청" aria-busy={visibleState.loading}>
            {visibleState.loading ? <p role="status" aria-live="polite">요청을 불러오는 중입니다…</p> : <>
              {visibleState.error && <p role="alert" className="platform-requests-error">{visibleState.error}</p>}
              {visibleState.error && <button type="button" className="platform-secondary" onClick={load} disabled={Boolean(busyId)}>다시 시도</button>}
              {visibleState.requests.length === 0 ? <p className="platform-empty">대기 중인 밴드 생성 요청이 없습니다.</p> : <ul className="platform-request-list">{visibleState.requests.map((request) => <li className="platform-request-card" key={request.id}>
                <div className="platform-request-copy"><div className="platform-request-title"><h2>{request.name || '이름 없는 요청'}</h2><span>승인 대기</span></div>
                  {request.slug && <p><strong>주소</strong><span>{request.slug}</span></p>}
                  {request.description && <p className="platform-request-description"><strong>소개</strong><span>{request.description}</span></p>}
                  {request.created_at && <p><strong>요청일</strong><time dateTime={request.created_at}>{new Date(request.created_at).toLocaleString('ko-KR')}</time></p>}
                </div>
                <div className="platform-request-actions"><button type="button" className="platform-secondary" disabled={Boolean(busyId)} onClick={() => decide(request, 'reject')}>{busyId === request.id ? '처리 중…' : '거절'}</button><button type="button" className="platform-primary" disabled={Boolean(busyId)} onClick={() => decide(request, 'approve')}>{busyId === request.id ? '처리 중…' : '승인'}</button></div>
              </li>)}</ul>}
            </>}
          </section>}
  </main>;
};
