import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { safeAuthReturnPath } from '../../services/authRedirect.js';
import { PolicyAcceptanceGate } from '../auth/PolicyAcceptanceGate.jsx';

const RequireAuth = ({ children }) => {
  const { loading, user } = useAuth();
  const location = useLocation();
  if (loading) return <main className="auth-page"><p>로그인 상태를 확인하는 중입니다…</p></main>;
  if (user) return <PolicyAcceptanceGate>{children}</PolicyAcceptanceGate>;
  const returnTo = safeAuthReturnPath(`${location.pathname}${location.search}`) || '/';
  return <Navigate to={`/login?returnTo=${encodeURIComponent(returnTo)}`} replace />;
};

export default RequireAuth;
