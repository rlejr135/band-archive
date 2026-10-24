import { Link } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import HeaderActions from './HeaderActions.jsx';

const GlobalShell = ({ children }) => {
  const { user } = useAuth();
  return <div className="global-shell">
    <header className="global-header">
      <Link to={user ? '/portal' : '/'} className="global-brand" aria-label="Band Archive 홈"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M8 24V8h8a5 5 0 0 1 3.5 8.6A5 5 0 0 1 17 26H8m5-13h3a2 2 0 1 0 0-4h-3m0 12h4a2 2 0 1 0 0-4h-4" /></svg></span><span>Band Archive<small>YOUR MUSIC, TOGETHER</small></span></Link>
      <nav className="global-nav" aria-label="주요 메뉴"><Link to="/guide">이용 안내</Link></nav>
      <div className="global-header-actions"><HeaderActions accountMode /></div>
    </header>
    <main className="global-main">{children}</main>
    <footer className="global-footer"><span>Band Archive</span><span>함께 만든 음악의 시간을 기록합니다.</span></footer>
  </div>;
};

export default GlobalShell;
