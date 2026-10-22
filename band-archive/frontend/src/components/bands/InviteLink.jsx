import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { BandContext } from '../../context/BandContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { BandIntroduction } from './BandIntroduction.jsx';
import { normalizedJoinName, validJoinName } from '../../services/joinName.js';
import { LoginPage } from '../auth/AuthPages.jsx';
import {
  clearInviteResumeContext,
  clearPendingInviteToken,
  captureInviteFragment,
  createInviteResumeContext,
  getInviteResumeContext,
  getInviteTokenGeneration,
  hasPendingInviteToken,
  inviteContinuationPath,
  inviteLinkPath,
  previewInviteLink,
  saveInviteResumeContext,
} from '../../services/inviteLinks.js';
import './InviteLink.css';

const apiError = (error) => {
  if (error?.status === 404) return '초대 링크가 만료되었거나 사용할 수 없습니다. 받은 사람에게 새 링크를 요청해 주세요.';
  if (error?.status === 401) return '로그인 상태를 확인할 수 없습니다. 다시 로그인해 주세요.';
  if (error?.status === 403) return '이 초대는 현재 로그인한 계정에서 이어갈 수 없습니다. 초대를 받은 이메일로 로그인해 주세요.';
  if (error?.status === 429) return '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.';
  return '초대 정보를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.';
};

const isInvitePayload = (value) => {
  const band = value?.invite?.band;
  const viewer = value?.viewer;
  return Boolean(value?.invite && (value.invite.id == null || (Number.isInteger(value.invite.id) && value.invite.id > 0))
    && band && Number.isInteger(band.id) && band.id > 0
    && typeof band.slug === 'string' && band.slug.length > 0
    && typeof band.name === 'string' && band.name.trim()
    && (band.description == null || typeof band.description === 'string')
    && (value.invite.featured_gallery == null || (typeof value.invite.featured_gallery === 'object' && !Array.isArray(value.invite.featured_gallery)))
    && Number.isInteger(value.invite.stats?.total_songs) && value.invite.stats.total_songs >= 0
    && value.invite.song_count_scope === 'PUBLIC'
    && (value.invite.announcement == null || typeof value.invite.announcement.content === 'string')
    && viewer && typeof viewer.can_open_workspace === 'boolean'
    && (viewer.membership_status == null || ['active', 'pending', 'left', 'rejected'].includes(viewer.membership_status)));
};

const toIntroduction = (payload) => ({
  band: payload.invite.band,
  featured_gallery: payload.invite.featured_gallery ?? null,
  stats: payload.invite.stats,
  song_count_scope: payload.invite.song_count_scope,
  announcement: payload.invite.announcement ?? null,
  viewer: payload.viewer,
});

const GuestActions = () => <section className="band-introduction-actions invite-actions" aria-label="밴드 초대">
  <p>이 초대 링크로 밴드에 가입할 수 있습니다. 로그인하면 이 화면에서 초대를 이어갑니다.</p>
  <LoginPage embedded returnToOverride={inviteContinuationPath} />
  <p className="bands-help">초대 확인에 실패하면 <Link to="/login">일반 로그인</Link> 후 원래 받은 초대 링크를 다시 열어 주세요.</p>
  <Link className="secondary-btn" to={`/signup?returnTo=${encodeURIComponent(inviteContinuationPath)}`}>회원가입하고 계속</Link>
</section>;

export const InviteLinkRoute = () => {
  captureInviteFragment();
  const location = useLocation();
  const { user, loading: authLoading, authGeneration = 0, getAccessToken, refreshSession } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [state, setState] = useState({ identity: '', loading: true, payload: null, error: '' });
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const [continuing, setContinuing] = useState(false);
  const actionControllerRef = useRef(null);
  const identityRef = useRef('');
  const previousUserIdRef = useRef(user?.id || null);
  const navigate = useNavigate();
  const identity = `${user?.id || 'anonymous'}:${authGeneration}:${getInviteTokenGeneration()}`;
  identityRef.current = identity;

  useEffect(() => () => { actionControllerRef.current?.abort(); actionControllerRef.current = null; }, [identity]);

  useEffect(() => {
    const nextUserId = user?.id || null;
    const previousUserId = previousUserIdRef.current;
    previousUserIdRef.current = nextUserId;
    if (!previousUserId && nextUserId && getInviteResumeContext()) {
      clearPendingInviteToken();
      navigate(inviteContinuationPath, { replace: true });
    }
  }, [navigate, user?.id]);

  useEffect(() => {
    const controller = new AbortController();
    if (!hasPendingInviteToken()) {
      setState({ identity, loading: false, payload: null, error: '초대 링크 정보를 찾을 수 없습니다. 원래 받은 초대 링크를 다시 열어 주세요.' });
      return () => controller.abort();
    }
    setState({ identity, loading: true, payload: null, error: '' });
    previewInviteLink({ signal: controller.signal }).then((payload) => {
      if (controller.signal.aborted || identityRef.current !== identity) return;
      if (!isInvitePayload(payload)) throw new Error('invalid_invite_preview');
      setState({ identity, loading: false, payload, error: '' });
    }).catch((error) => {
      if (!controller.signal.aborted && error?.name !== 'AbortError') setState({ identity, loading: false, payload: null, error: apiError(error) });
    });
    return () => controller.abort();
  }, [api, identity, location.pathname, location.search, location.hash, previewAttempt]);

  const continueSignedIn = async () => {
    if (!user?.email || continuing) return;
    actionControllerRef.current?.abort();
    const controller = new AbortController();
    actionControllerRef.current = controller;
    setContinuing(true);
    setState((previous) => ({ ...previous, error: '' }));
    try {
      const context = await createInviteResumeContext(user.email, { signal: controller.signal });
      const payload = await api.requestJson(inviteLinkPath('/resume'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ context }), cache: 'no-store', signal: controller.signal,
      });
      if (controller.signal.aborted || identityRef.current !== identity) return;
      if (!isInvitePayload(payload)) throw new Error('invalid_invite_resume');
      clearPendingInviteToken();
      saveInviteResumeContext(context);
      navigate(inviteContinuationPath, { replace: true });
    } catch (error) {
      if (!controller.signal.aborted && identityRef.current === identity) setState((previous) => previous.identity === identity ? { ...previous, error: apiError(error) } : previous);
    } finally { if (!controller.signal.aborted) setContinuing(false); }
  };

  if (state.identity !== identity || state.loading) return <main className="band-introduction-page"><p role="status">초대받은 밴드 소개를 불러오는 중입니다…</p></main>;
  if (!state.payload) return <main className="band-introduction-page"><Link className="bands-return-link" to="/bands/discover">← 공개 밴드 찾기</Link><section className="invite-error" role="alert"><h1>초대 링크를 확인할 수 없습니다</h1><p>{hasPendingInviteToken() ? state.error : '로그인 후 다시 초대 링크를 열어 주세요. 링크 정보가 브라우저에 남아 있지 않습니다.'}</p>{hasPendingInviteToken() && <button className="secondary-btn" type="button" onClick={() => { setState((previous) => ({ ...previous, loading: true, error: '' })); setPreviewAttempt((attempt) => attempt + 1); }}>다시 시도</button>}<p><Link to="/login">로그인</Link></p></section></main>;
  const introduction = toIntroduction(state.payload);
  const viewer = state.payload.viewer;
  return <BandIntroduction introduction={introduction}>
    {state.error && <p className="auth-error" role="alert">{state.error}</p>}
    {viewer.can_open_workspace
      ? <section className="band-introduction-actions"><p>이미 가입한 밴드입니다.</p><Link className="primary-btn" to={`/bands/${encodeURIComponent(introduction.band.slug)}`}>밴드 공간 열기</Link></section>
      : authLoading
        ? <section className="band-introduction-actions invite-actions"><p role="status">로그인 상태를 확인하고 있습니다…</p></section>
        : user
        ? <section className="band-introduction-actions invite-actions"><p>로그인한 계정으로 초대 내용을 확인한 뒤 가입을 진행할 수 있습니다.</p><button className="primary-btn" type="button" onClick={continueSignedIn} disabled={continuing}>{continuing ? '초대 확인 중…' : '초대 확인하고 계속'}</button></section>
        : <GuestActions />}
  </BandIntroduction>;
};

export const InviteContinueRoute = () => {
  const { user, authGeneration = 0, getAccessToken, refreshSession } = useAuth();
  const bands = useContext(BandContext);
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [state, setState] = useState({ identity: '', loading: true, payload: null, error: '' });
  const [resumeAttempt, setResumeAttempt] = useState(0);
  const [acceptingIdentity, setAcceptingIdentity] = useState('');
  const actionControllerRef = useRef(null);
  const identityRef = useRef('');
  const navigate = useNavigate();
  const context = getInviteResumeContext();
  const accountIdentity = `${user?.id || 'anonymous'}:${authGeneration}`;
  const identity = `${accountIdentity}:${context}`;
  const accepting = acceptingIdentity === identity;
  identityRef.current = identity;
  const draftIdentity = identity;
  const [nameDraft, setNameDraft] = useState({ identity: draftIdentity, value: '' });
  const name = nameDraft.identity === draftIdentity ? nameDraft.value : '';

  useEffect(() => {
    if (!context && hasPendingInviteToken()) navigate('/invite', { replace: true });
  }, [context, navigate]);

  useEffect(() => () => { actionControllerRef.current?.abort(); actionControllerRef.current = null; }, [identity]);

  useEffect(() => {
    const controller = new AbortController();
    if (!context) {
      setState({ identity, loading: false, payload: null, error: '초대 확인 정보가 없습니다. 원래 받은 초대 링크를 다시 열어 주세요.' });
      return () => controller.abort();
    }
    setState({ identity, loading: true, payload: null, error: '', retryable: false });
    api.requestJson(inviteLinkPath('/resume'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ context }), signal: controller.signal, cache: 'no-store',
    }).then((payload) => {
      if (controller.signal.aborted || identityRef.current !== identity) return;
      if (!isInvitePayload(payload)) throw new Error('invalid_invite_resume');
      clearPendingInviteToken();
      if (payload.expires_at) saveInviteResumeContext(context, payload.expires_at);
      setState({ identity, loading: false, payload, error: '' });
    }).catch((error) => {
      if (!controller.signal.aborted && error?.name !== 'AbortError') {
        if (error?.status === 403 || error?.status === 404) clearInviteResumeContext();
        setState({ identity, loading: false, payload: null, error: apiError(error), retryable: error?.status !== 403 && error?.status !== 404 });
      }
    });
    return () => controller.abort();
  }, [api, context, identity, resumeAttempt]);

  const accept = useCallback(async () => {
    if (!context || accepting || !validJoinName(name)) return;
    actionControllerRef.current?.abort();
    const controller = new AbortController();
    actionControllerRef.current = controller;
    setAcceptingIdentity(identity);
    setState((previous) => ({ ...previous, error: '' }));
    try {
      const result = await api.requestJson(inviteLinkPath('/accept'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ context, name: normalizedJoinName(name) }), cache: 'no-store', signal: controller.signal,
      });
      if (controller.signal.aborted || identityRef.current !== identity) return;
      if (result?.joined !== true && result?.already_member !== true) throw new Error('invalid_invite_accept');
      if (!result.band || typeof result.band.slug !== 'string' || !result.band.slug) throw new Error('invalid_invite_accept');
      clearInviteResumeContext();
      // Joining succeeded even if the membership-list refresh later fails.
      try { Promise.resolve(bands?.reload?.({ preserveSelection: true })).catch(() => {}); } catch { /* Navigation must not turn a successful join into an error. */ }
      navigate(`/bands/${encodeURIComponent(result.band.slug)}`, { replace: true });
    } catch (error) { if (!controller.signal.aborted && identityRef.current === identity) setState((previous) => previous.identity === identity ? { ...previous, error: apiError(error) } : previous); }
    finally { if (!controller.signal.aborted) setAcceptingIdentity((previous) => previous === identity ? '' : previous); }
  }, [accepting, api, context, identity, name, bands, navigate]);

  if (state.identity !== identity || state.loading) return <main className="band-introduction-page"><p role="status">초대 상태를 확인하는 중입니다…</p></main>;
  if (!state.payload) return <main className="band-introduction-page"><Link className="bands-return-link" to="/bands">← 내 밴드</Link><section className="invite-error" role="alert"><h1>초대를 이어갈 수 없습니다</h1><p>{state.error}</p>{state.retryable && context && <button type="button" className="secondary-btn" onClick={() => { setState((previous) => ({ ...previous, loading: true, error: '' })); setResumeAttempt((attempt) => attempt + 1); }}>다시 시도</button>}<p>초대 정보가 만료되었거나 브라우저에서 사라졌다면 로그인 후 원래 받은 초대 링크를 다시 열어 주세요.</p><p><Link to="/bands/discover">공개 밴드 찾기</Link></p></section></main>;
  const introduction = toIntroduction(state.payload);
  const viewer = state.payload.viewer;
  return <BandIntroduction introduction={introduction}>
    {state.error && <p className="auth-error" role="alert">{state.error}</p>}
    {viewer.can_open_workspace
      ? <section className="band-introduction-actions"><p>이미 가입한 밴드입니다.</p><Link className="primary-btn" to={`/bands/${encodeURIComponent(introduction.band.slug)}`}>밴드 공간 열기</Link></section>
      : <section className="band-introduction-actions invite-actions"><h2>{introduction.band.name} 밴드에 가입하시겠습니까?</h2><p>{viewer.membership_status === 'pending' ? '대기 중인 가입 요청은 초대 수락과 함께 활성 멤버십으로 전환됩니다.' : `${user?.email || '로그인 계정'}으로 초대받은 밴드에 가입합니다.`}</p>
        <form className="bands-form" onSubmit={(event) => { event.preventDefault(); accept(); }} aria-busy={accepting}>
          <label htmlFor="invite-member-name">밴드에서 사용할 이름 (필수)<input id="invite-member-name" value={name} onChange={(event) => setNameDraft({ identity: draftIdentity, value: event.target.value })} required maxLength={100} autoComplete="name" disabled={accepting} aria-describedby="invite-member-name-help" /></label>
          <p className="bands-help" id="invite-member-name-help">멤버 목록에 표시할 이름을 100자 이내로 입력해 주세요. 기존에 연결된 멤버 프로필이 있다면 이름은 유지됩니다.</p>
          <button className="primary-btn" type="submit" disabled={accepting || !validJoinName(name)}>{accepting ? '가입 중…' : '초대받은 밴드에 가입'}</button>
        </form><p className="bands-help">가입 버튼을 누르기 전에는 밴드 멤버가 되지 않습니다.</p></section>}
  </BandIntroduction>;
};
