import { Link, Navigate, Route, Routes, useParams } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext.jsx';
import { BandProvider } from './context/BandContext.jsx';
import { UploadProvider } from './context/UploadContext.jsx';
import { ServicePolicyProvider, useServicePolicy } from './context/ServicePolicyContext.jsx';
import { PolicyPage, PolicyLinks } from './components/auth/PolicyPages.jsx';
import { LoginPage, SignupPage, VerifyEmailPage, ForgotPasswordPage, ResetPasswordPage } from './components/auth/AuthPages.jsx';
import { BandsPage } from './components/bands/BandsPage.jsx';
import { BandCreationRequestsAdminRoute } from './components/bands/BandCreationRequestsAdmin.jsx';
import { BandCreationRequestPage, BandManageRoute, DiscoverBandsPage } from './components/bands/BandOnboarding.jsx';
import RequireAuth from './components/layout/RequireAuth.jsx';
import { AvailabilityWorkspace, BandDashboard, BandWorkspaceRoute, GalleryWorkspace, MemberDetailWorkspace, MembersWorkspace, RehearsalWorkspace, SongWorkspace, SuggestionsWorkspace } from './components/workspace/BandWorkspace.jsx';
import './App.css';
import './components/bands/BandsPage.css';
import './components/home/WelcomeLanding.css';

const WelcomeLanding = ({ authRoute = false }) => {
  const { config: serviceConfig } = useServicePolicy();
  const serviceName = serviceConfig?.service?.name || '밴드 아카이브';
  const { user, loading } = useAuth();
  if (loading) return <main className="home-loading" aria-live="polite">로그인 상태를 확인하고 있습니다…</main>;
  if (user && !authRoute) return <Navigate to="/bands" replace />;

  return <main className="welcome-page">
    <header className="welcome-header">
      <Link className="welcome-brand" to="/" aria-label={`${serviceName} 홈`}><span className="welcome-brand-mark">B</span><span>{serviceName} <small>YOUR MUSIC, TOGETHER</small></span></Link>
      <a className="welcome-header-link" href="#login">로그인</a>
    </header>
    <section className="welcome-hero">
      <div className="welcome-copy">
        <p className="welcome-eyebrow">YOUR BAND, IN ONE PLACE</p>
        <h1>함께 만든 순간을<br /><em>오래 남기다</em></h1>
        <p className="welcome-description">곡과 합주, 멤버와 공연 기록까지. 밴드의 시간을 한곳에 모으고 함께 이어가세요.</p>
        <a className="welcome-cta" href="#login">밴드 아카이브 시작하기 <span aria-hidden="true">↗</span></a>
        <p className="welcome-note">초대받은 밴드 멤버라면 계정으로 로그인해 주세요.</p>
      </div>
      <div className="welcome-preview-wrap" aria-label="대시보드 예시 미리보기">
        <div className="welcome-preview-label"><span className="preview-dot" /> 미리보기 · 예시 화면</div>
        <div className="welcome-preview">
          <div className="preview-top"><div><span className="preview-kicker">BAND ARCHIVE</span><h2>우리의 무대 뒤편</h2></div><span className="preview-avatar">A</span></div>
          <div className="preview-tabs" aria-label="예시 화면의 장식 요소"><span className="is-current">한눈에 보기</span><span>곡</span><span>합주</span><span>기록</span></div>
          <div className="preview-stats"><div><span>함께한 곡</span><strong>24</strong><small>우리의 레퍼토리</small></div><div><span>기록한 합주</span><strong>18</strong><small>쌓여가는 시간</small></div></div>
          <div className="preview-recent"><div className="preview-record-icon">♫</div><div><span>최근의 기록</span><strong>여름밤 라이브 세션</strong><small>새로운 순간이 아카이브에 담겼어요</small></div><span className="preview-arrow">↗</span></div>
          <div className="preview-disclaimer">실제 밴드 정보가 아닌 화면 예시입니다</div>
        </div>
        <div className="welcome-orbit welcome-orbit-one" /><div className="welcome-orbit welcome-orbit-two" />
      </div>
    </section>
    <section className="welcome-login" id="login" aria-labelledby="welcome-login-title">
      <div><p className="welcome-eyebrow">WELCOME BACK</p><h2 id="welcome-login-title">아카이브로 돌아오기</h2><p>로그인하면 내 밴드 목록에서 작업할 공간을 선택할 수 있어요.</p></div>
      <LoginPage embedded />
    </section>
    <footer className="welcome-footer"><span>{serviceName}</span><PolicyLinks /><span>밴드의 시간을 함께 기록합니다.</span></footer>
  </main>;
};

const RootRoute = () => {
  const { user, loading } = useAuth();
  if (loading) return <main className="home-loading" aria-live="polite">로그인 상태를 확인하고 있습니다…</main>;
  return user ? <Navigate to="/bands" replace /> : <WelcomeLanding />;
};

const LegacyRoute = () => {
  const { user, loading } = useAuth();
  if (loading) return <main className="home-loading" aria-live="polite">로그인 상태를 확인하고 있습니다…</main>;
  return user ? <Navigate to="/bands" replace /> : <Navigate to="/" replace />;
};

const BandAliasRedirect = () => {
  const { bandSlug, '*': rest } = useParams();
  const suffix = rest ? `/${rest}` : '';
  return <Navigate to={`/bands/${encodeURIComponent(bandSlug)}${suffix}`} replace />;
};

export const AppRoutes = () => <Routes>
  <Route path="/" element={<RootRoute />} />
  <Route path="/login" element={<WelcomeLanding authRoute />} />
  <Route path="/signup" element={<SignupPage />} />
  <Route path="/privacy" element={<PolicyPage kind="privacy" />} />
  <Route path="/terms" element={<PolicyPage kind="terms" />} />
  <Route path="/verify-email" element={<VerifyEmailPage />} />
  <Route path="/forgot-password" element={<ForgotPasswordPage />} />
  <Route path="/reset-password" element={<ResetPasswordPage />} />
  <Route path="/band/:bandSlug/*" element={<BandAliasRedirect />} />
  <Route path="/bands" element={<RequireAuth><BandsPage /></RequireAuth>} />
  <Route path="/bands/discover" element={<RequireAuth><DiscoverBandsPage /></RequireAuth>} />
  <Route path="/bands/request" element={<RequireAuth><BandCreationRequestPage /></RequireAuth>} />
  <Route path="/platform/band-requests" element={<RequireAuth><BandCreationRequestsAdminRoute /></RequireAuth>} />
  <Route path="/bands/:bandSlug/manage" element={<RequireAuth><BandManageRoute /></RequireAuth>} />
  <Route path="/bands/:bandSlug" element={<RequireAuth><BandWorkspaceRoute /></RequireAuth>}>
    <Route index element={<BandDashboard />} />
    <Route path="songs" element={<SongWorkspace />} />
    <Route path="rehearsals" element={<RehearsalWorkspace />} />
    <Route path="availability" element={<AvailabilityWorkspace />} />
    <Route path="members" element={<MembersWorkspace />} />
    <Route path="members/:memberId" element={<MemberDetailWorkspace />} />
    <Route path="gallery" element={<GalleryWorkspace />} />
    <Route path="suggestions" element={<SuggestionsWorkspace />} />
  </Route>
  <Route path="/bands/*" element={<RequireAuth><Navigate to="/bands" replace /></RequireAuth>} />
  <Route path="*" element={<LegacyRoute />} />
</Routes>;

function App() {
  return <ServicePolicyProvider><AuthProvider><BandProvider><UploadProvider><AppRoutes /></UploadProvider></BandProvider></AuthProvider></ServicePolicyProvider>;
}

export default App;
