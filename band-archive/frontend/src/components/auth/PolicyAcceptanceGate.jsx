import { useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../../context/AuthContext.jsx';
import { useServicePolicy } from '../../context/ServicePolicyContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { PolicyChecks, PolicyLinks } from './PolicyPages.jsx';

const AccountPolicyGate = ({ children }) => {
  const { user, getAccessToken, refreshSession, signOut } = useAuth();
  const { config } = useServicePolicy();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [state, setState] = useState('loading');
  const [attempt, setAttempt] = useState(0);
  const [terms, setTerms] = useState(false); const [age, setAge] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const mutation = useRef(null);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; mutation.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    api.requestJson('/api/v2/me', { signal: controller.signal }).then((data) => {
      if (typeof data.user?.policy_acceptance_required !== 'boolean') throw new Error('invalid_policy_status');
      if (!controller.signal.aborted) setState(data.user.policy_acceptance_required ? 'required' : 'accepted');
    }).catch(() => { if (!controller.signal.aborted) setState('error'); });
    return () => controller.abort();
  }, [api, attempt, user.id]);
  const submit = async (event) => {
    event.preventDefault(); if (!terms || !age || mutation.current || !mounted.current) return;
    const controller = new AbortController(); mutation.current = controller;
    setBusy(true); setError('');
    try {
      const receipt = await api.requestJson('/api/v2/me/policy-acceptance', { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ terms_version: config.policies.terms_version, privacy_version: config.policies.privacy_version, age_14_plus: true, terms_accepted: true }) });
      if (receipt?.accepted !== true) throw new Error('invalid_receipt');
      if (!controller.signal.aborted && mounted.current) setState('accepted');
    } catch { if (!controller.signal.aborted && mounted.current) setError('가입 확인을 저장하지 못했습니다. 정책이 변경되었을 수 있으니 새로고침 후 다시 확인해 주세요.'); }
    finally { if (mutation.current === controller) mutation.current = null; if (!controller.signal.aborted && mounted.current) setBusy(false); }
  };
  if (state === 'accepted') return children;
  return <main className="auth-page"><section className="auth-card"><h1>가입 확인</h1>{state === 'loading' ? <p role="status">가입 확인 상태를 불러오는 중입니다…</p> : state === 'error' ? <><p role="alert">가입 확인 상태를 불러오지 못했습니다.</p><button className="secondary-btn" onClick={() => { setState('loading'); setAttempt((value) => value + 1); }}>다시 시도</button></> : <form className="auth-form" onSubmit={submit}><p>이메일 확인이 완료되었습니다. 이용약관과 개인정보처리방침을 확인하고 가입을 마무리해 주세요.</p><PolicyChecks terms={terms} age={age} onTerms={setTerms} onAge={setAge} />{error && <p role="alert">{error}</p>}<button className="primary-btn" disabled={!terms || !age || busy}>{busy ? '확인 중…' : '확인하고 계속하기'}</button></form>}<PolicyLinks /><button className="secondary-btn" onClick={signOut}>로그아웃</button></section></main>;
};

export const PolicyAcceptanceGate = ({ children }) => {
  const { user, authGeneration = 0 } = useAuth();
  const { config } = useServicePolicy();
  if (!user || !config?.policies?.ready || !config.policies.policy_acceptance_enabled) return children;
  return <AccountPolicyGate key={`${user.id}:${authGeneration}:${config.policies.terms_version}:${config.policies.privacy_version}`} >{children}</AccountPolicyGate>;
};
