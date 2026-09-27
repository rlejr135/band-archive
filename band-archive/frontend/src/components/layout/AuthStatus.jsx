import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';

const AuthStatus = () => {
  const navigate = useNavigate();
  const { user, loading, signOut } = useAuth();
  const { selectedBand } = useBands();
  if (loading) return null;
  if (!user) return <button className="nav-btn" onClick={() => navigate('/login')}>로그인</button>;
  const logout = async () => {
    await signOut();
    navigate('/', { replace: true });
  };
  return <>
    <button className="nav-btn" onClick={() => navigate('/bands')}>{selectedBand ? `🎸 ${selectedBand.name}` : '🎸 밴드 선택'}</button>
    <span className="auth-account" title={user.email || undefined}>내 계정</span>
    <button className="nav-btn" onClick={logout}>로그아웃</button>
  </>;
};

export default AuthStatus;
