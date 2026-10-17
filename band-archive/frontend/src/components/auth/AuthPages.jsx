import { useMemo, useRef, useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { normalizeEmail, safeReturnPath } from '../../services/authRedirect.js';
import { getSupabaseClient } from '../../services/supabase.js';
import { createSubmissionGuard } from '../../services/authSubmission.js';
import { useServicePolicy } from '../../context/ServicePolicyContext.jsx';
import { PolicyChecks, PolicyLinks } from './PolicyPages.jsx';
import { TurnstileChallenge } from './TurnstileChallenge.jsx';
import './AuthPages.css';

const authFailure = '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.';

const appOrigin = () => window.location.origin;
const returnFromSearch = (searchParams) => safeReturnPath(searchParams.get('returnTo')) || '/bands';

const useAuthChallenge = () => {
  const policy = useServicePolicy();
  const [token, setToken] = useState('');
  const [resetKey, setResetKey] = useState(0);
  const enabled = policy.config?.captcha?.enabled === true;
  return { ...policy, token, blocked: policy.loading || Boolean(policy.error) || (enabled && (!policy.config.captcha.site_key || !token)),
    reset: () => { setToken(''); setResetKey((value) => value + 1); },
    widget: enabled ? <TurnstileChallenge siteKey={policy.config.captcha.site_key} onToken={setToken} resetKey={resetKey} /> : null,
    notice: policy.loading ? <p role="status">서비스 설정을 확인하고 있습니다…</p> : policy.error ? <><p role="alert">{policy.error}</p><button className="secondary-btn" type="button" onClick={policy.retry}>설정 다시 불러오기</button></> : null };
};

const AuthFrame = ({ title, children, embedded = false }) => {
  const { configError } = useAuth();
  const card = <section className={`auth-card${embedded ? ' auth-card--embedded' : ''}`} aria-labelledby="auth-title">
    <h1 id="auth-title">{title}</h1>
    {configError && <p className="auth-config-error" role="alert">{configError}</p>}
    {children}
    <PolicyLinks />
  </section>;
  return (
    embedded ? card : <main className="auth-page">{card}</main>
  );
};

const AuthForm = ({ children, onSubmit, submitting }) => (
  <form className="auth-form" onSubmit={onSubmit} aria-busy={submitting}>
    {children}
  </form>
);

export const LoginPage = ({ embedded = false }) => {
  const challenge = useAuthChallenge();
  const { user, loading, configError } = useAuth();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const returnTo = useMemo(() => returnFromSearch(searchParams), [searchParams]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const submission = useRef(createSubmissionGuard()).current;

  if (!loading && user) return <Navigate to={returnTo} replace />;
  const submit = async (event) => {
    event.preventDefault();
    if (configError || challenge.blocked || !submission.tryAcquire()) return;
    setSubmitting(true);
    setError('');
    try {
      const supabase = getSupabaseClient();
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: normalizeEmail(email), password, options: { captchaToken: challenge.token || undefined },
      });
      if (signInError?.code === 'captcha_failed') { setError('봇 방지 설정이 변경되었거나 확인이 만료되었습니다. 다시 확인한 뒤 로그인해 주세요.'); challenge.retry?.(); }
      else if (signInError) setError(authFailure);
      else navigate(returnTo, { replace: true });
    } catch {
      setError(authFailure);
    } finally {
      submission.release();
      challenge.reset();
      setSubmitting(false);
    }
  };
  return (
    <AuthFrame title="로그인" embedded={embedded}>
      <AuthForm onSubmit={submit} submitting={submitting}>
        <label>이메일<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        <label>비밀번호<input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        {error && <p className="auth-error" role="alert">{error}</p>}
        {challenge.notice}{challenge.widget}
        <button className="primary-btn" disabled={submitting || Boolean(configError) || challenge.blocked}>{submitting ? '로그인 중…' : '로그인'}</button>
      </AuthForm>
      <p><Link to={`/signup?returnTo=${encodeURIComponent(returnTo)}`}>회원가입</Link> · <Link to="/forgot-password">비밀번호를 잊으셨나요?</Link></p>
    </AuthFrame>
  );
};

export const SignupPage = () => {
  const challenge = useAuthChallenge();
  const policyRequired = challenge.config?.policies?.policy_acceptance_enabled === true;
  const versionPair = `${challenge.config?.policies?.terms_version || ''}:${challenge.config?.policies?.privacy_version || ''}`;
  const [checks, setChecks] = useState({ versionPair, terms: false, age: false });
  const terms = checks.versionPair === versionPair && checks.terms;
  const age = checks.versionPair === versionPair && checks.age;
  const setTerms = (value) => setChecks((previous) => ({ versionPair, terms: value, age: previous.versionPair === versionPair && previous.age }));
  const setAge = (value) => setChecks((previous) => ({ versionPair, age: value, terms: previous.versionPair === versionPair && previous.terms }));
  const { configError } = useAuth();
  const [searchParams] = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState('');
  const submission = useRef(createSubmissionGuard()).current;
  const returnTo = useMemo(() => returnFromSearch(searchParams), [searchParams]);
  const submit = async (event) => {
    event.preventDefault();
    if (configError || challenge.blocked || (policyRequired && (!challenge.config?.policies?.ready || !terms || !age)) || !submission.tryAcquire()) return;
    setSubmitting(true);
    setError('');
    try {
      const { error: signUpError } = await getSupabaseClient().auth.signUp({
        email: normalizeEmail(email),
        password,
        options: { emailRedirectTo: `${appOrigin()}/verify-email`, captchaToken: challenge.token || undefined },
      });
      if (signUpError?.code === 'captcha_failed') { setError('봇 방지 설정이 변경되었거나 확인이 만료되었습니다. 다시 확인한 뒤 가입해 주세요.'); challenge.retry?.(); }
      else if (signUpError) setError(authFailure);
      else setComplete(true);
    } catch {
      setError(authFailure);
    } finally {
      submission.release();
      challenge.reset();
      setSubmitting(false);
    }
  };
  return (
    <AuthFrame title="회원가입">
      {complete ? <p className="auth-success" role="status">입력한 주소로 확인 메일을 보냈습니다. 메일의 링크를 연 뒤 로그인해 주세요.</p> : <>
        <p className="auth-help">가입을 완료하려면 이메일 확인이 필요합니다.</p>
        <AuthForm onSubmit={submit} submitting={submitting}>
          <label>이메일<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label>비밀번호<input type="password" autoComplete="new-password" minLength="8" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          {policyRequired && <><PolicyChecks terms={terms} age={age} onTerms={setTerms} onAge={setAge} /><p className="auth-help">이메일 확인 후 로그인하면 최종 가입 확인을 한 번 더 진행합니다.</p></>}
          <p className="auth-help">밴드 탈퇴 후에도 작성 콘텐츠는 남으며 owner·superadmin만 삭제할 수 있습니다.</p>
          {error && <p className="auth-error" role="alert">{error}</p>}
          {challenge.notice}{challenge.widget}
          <button className="primary-btn" disabled={submitting || Boolean(configError) || challenge.blocked || (policyRequired && (!challenge.config?.policies?.ready || !terms || !age))}>{submitting ? '가입 요청 중…' : '회원가입'}</button>
        </AuthForm>
      </>}
      <p><Link to={`/login?returnTo=${encodeURIComponent(returnTo)}`}>로그인으로 돌아가기</Link></p>
    </AuthFrame>
  );
};

export const VerifyEmailPage = () => {
  const { user, loading } = useAuth();
  return (
    <AuthFrame title="이메일 확인">
      {loading ? <p>확인 상태를 불러오는 중입니다…</p> : user ? <p className="auth-success" role="status">이메일 확인이 완료되었습니다. 밴드를 선택해 계속하세요.</p> : <p>확인 메일의 링크를 열어 이메일 인증을 완료해 주세요.</p>}
      <p><Link to="/login">로그인</Link> · <Link to="/bands">밴드 선택</Link></p>
    </AuthFrame>
  );
};

export const ForgotPasswordPage = () => {
  const challenge = useAuthChallenge();
  const { configError } = useAuth();
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  const submission = useRef(createSubmissionGuard()).current;
  const submit = async (event) => {
    event.preventDefault();
    if (configError || challenge.blocked || !submission.tryAcquire()) return;
    setSubmitting(true);
    setError('');
    let captchaFailed = false;
    try {
      const { error: resetError } = await getSupabaseClient().auth.resetPasswordForEmail(normalizeEmail(email), {
        redirectTo: `${appOrigin()}/reset-password`,
        captchaToken: challenge.token || undefined,
      });
      if (resetError?.code === 'captcha_failed') { captchaFailed = true; setError('봇 방지 설정이 변경되었거나 확인이 만료되었습니다. 다시 확인한 뒤 요청해 주세요.'); challenge.retry?.(); }
    } catch {
      // The response remains identical for missing accounts and transport failures.
    } finally {
      // This is intentionally identical whether the address has an account.
      setSent(!captchaFailed);
      submission.release();
      challenge.reset();
      setSubmitting(false);
    }
  };
  return (
    <AuthFrame title="비밀번호 재설정">
      {sent ? <p className="auth-success" role="status">해당 주소에 계정이 있다면 재설정 메일을 보냈습니다.</p> : <AuthForm onSubmit={submit} submitting={submitting}>
        <label>이메일<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        {error && <p className="auth-error" role="alert">{error}</p>}
        {challenge.notice}{challenge.widget}
        <button className="primary-btn" disabled={submitting || Boolean(configError) || challenge.blocked}>{submitting ? '요청 중…' : '재설정 메일 보내기'}</button>
      </AuthForm>}
      <p><Link to="/login">로그인으로 돌아가기</Link></p>
    </AuthFrame>
  );
};

export const ResetPasswordPage = () => {
  const { configError, hasRecoverySession, loading, clearRecoverySession } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const submission = useRef(createSubmissionGuard()).current;
  const submit = async (event) => {
    event.preventDefault();
    if (!hasRecoverySession || configError || !submission.tryAcquire()) return;
    setSubmitting(true);
    try {
      const { error: updateError } = await getSupabaseClient().auth.updateUser({ password });
      if (updateError) setError(authFailure);
      else {
        clearRecoverySession();
        navigate('/login', { replace: true });
      }
    } catch {
      setError(authFailure);
    } finally {
      submission.release();
      setSubmitting(false);
    }
  };
  return (
    <AuthFrame title="새 비밀번호 설정">
      {loading ? <p>재설정 링크를 확인하는 중입니다…</p> : !hasRecoverySession ? <p className="auth-error" role="alert">유효한 비밀번호 재설정 링크가 필요합니다. 새 링크를 요청해 주세요.</p> : <AuthForm onSubmit={submit} submitting={submitting}>
        <label>새 비밀번호<input type="password" autoComplete="new-password" minLength="8" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button className="primary-btn" disabled={submitting || Boolean(configError)}>{submitting ? '저장 중…' : '비밀번호 저장'}</button>
      </AuthForm>}
      <p><Link to="/forgot-password">새 재설정 링크 요청</Link></p>
    </AuthFrame>
  );
};
