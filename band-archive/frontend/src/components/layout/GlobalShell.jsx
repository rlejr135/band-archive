import { Link } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import AuthStatus from './AuthStatus.jsx';
import BandSwitcher from '../bands/BandSwitcher.jsx';

const GlobalShell = ({ children }) => {
  const { user } = useAuth();
  return <div className="global-shell">
    <header className="global-header">
      <Link to={user ? '/portal' : '/'} className="global-brand" aria-label="Band Archive 홈"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M8 24V8h8a5 5 0 0 1 3.5 8.6A5 5 0 0 1 17 26H8m5-13h3a2 2 0 1 0 0-4h-3m0 12h4a2 2 0 1 0 0-4h-4" /></svg></span><span>Band Archive<small>YOUR MUSIC, TOGETHER</small></span></Link>
      <nav className="global-nav" aria-label="주요 메뉴"><Link to="/guide">이용 안내</Link>{user && <Link className="global-settings-link" to="/account" aria-label="계정 설정" title="계정 설정"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm8 3.5a8.1 8.1 0 0 0-.1-1.3l1.5-1.2-1.5-2.6-1.8.7a7.9 7.9 0 0 0-2.2-1.3L15.6 4h-3.1l-.3 1.9A7.9 7.9 0 0 0 10 7.2l-1.8-.7-1.5 2.6 1.5 1.2a8.1 8.1 0 0 0 0 2.6l-1.5 1.2 1.5 2.6 1.8-.7a7.9 7.9 0 0 0 2.2 1.3l.3 1.9h3.1l.3-1.9a7.9 7.9 0 0 0 2.2-1.3l1.8.7 1.5-2.6-1.5-1.2c.1-.4.1-.8.1-1.2Z" /></svg></Link>}</nav>
      <div className="global-header-actions">{user && <BandSwitcher accountMode />}<AuthStatus /></div>
    </header>
    <main className="global-main">{children}</main>
    <footer className="global-footer"><span>Band Archive</span><span>함께 만든 음악의 시간을 기록합니다.</span></footer>
  </div>;
};

export default GlobalShell;
