import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useWorkspace } from './WorkspaceContext.jsx';

export const WriteGuidance = () => {
  const { band } = useWorkspace();
  const { user } = useAuth();
  const navigate = useNavigate();
  if (band.membership) return null;
  return <aside className="workspace-guidance">{user ? '이 밴드의 멤버가 되면 곡과 합주를 작성할 수 있습니다.' : <><span>작성하려면 로그인 후 밴드에 가입해 주세요.</span><button className="secondary-btn" onClick={() => navigate('/login')}>로그인</button></>}</aside>;
};
