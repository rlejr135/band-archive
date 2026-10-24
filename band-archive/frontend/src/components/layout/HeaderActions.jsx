import { useEffect, useId, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import BandSwitcher from '../bands/BandSwitcher.jsx';
import './HeaderActions.css';

const HeaderIcon = ({ kind }) => <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">{kind === 'settings' ? <><path d="m9 3-.6 2.1-1.9 1.1-2.1-.5-2 3.5L4 10.8v2.4l-1.6 1.6 2 3.5 2.1-.5 1.9 1.1L9 21h4l.6-2.1 1.9-1.1 2.1.5 2-3.5-1.6-1.6v-2.4l1.6-1.6-2-3.5-2.1.5-1.9-1.1L13 3Z" /><circle cx="11" cy="12" r="3" /></> : <><circle cx="12" cy="8" r="3" /><path d="M5 21v-2a7 7 0 0 1 14 0v2" /></>}</svg>;

const HeaderActionsContent = ({ currentSlug, accountMode = false }) => {
  const { user, loading, signOut } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const panelId = useId();
  const [openPath, setOpenPath] = useState(null);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState('');
  const container = useRef(null);
  const trigger = useRef(null);
  const open = openPath === pathname;
  useEffect(() => {
    if (!open) return undefined;
    const outside = (event) => { if (!container.current?.contains(event.target)) setOpenPath(null); };
    const escape = (event) => { if (event.key === 'Escape') { setOpenPath(null); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  const logout = async () => {
    if (signingOut) return;
    setSigningOut(true); setError('');
    try { await signOut(); navigate('/', { replace: true }); }
    catch { setError('로그아웃하지 못했습니다. 다시 시도해 주세요.'); setSigningOut(false); }
  };
  if (loading) return null;
  if (!user) return <div className="header-actions"><Link className="header-action" to="/login">로그인</Link></div>;
  return <div className="header-actions">
    <BandSwitcher currentSlug={currentSlug} accountMode={accountMode} />
    <Link className="header-action header-settings" to="/account" aria-label="계정 설정"><HeaderIcon kind="settings" /><span>설정</span></Link>
    <div className="header-account" ref={container}>
      <button className="header-action" type="button" ref={trigger} aria-label="계정 메뉴" aria-expanded={open} aria-controls={panelId} onClick={() => setOpenPath(open ? null : pathname)}><HeaderIcon kind="account" /><span>계정</span><span className="header-chevron" aria-hidden="true">⌄</span></button>
      {open && <div id={panelId} className="header-account-panel">
        <span className="header-account-caption">내 계정</span>
        <Link to="/portal" onClick={() => setOpenPath(null)}>홈으로</Link>
        <Link to="/account" onClick={() => setOpenPath(null)}>계정과 밴드 관리</Link>
        <button type="button" disabled={signingOut} onClick={logout}>{signingOut ? '로그아웃 중…' : '로그아웃'}</button>
        {error && <p role="alert">{error}</p>}
      </div>}
    </div>
  </div>;
};

// A route transition destroys disclosure state. Returning to the same history
// entry must not restore a menu that was open before leaving that page.
const HeaderActions = (props) => {
  const location = useLocation();
  return <HeaderActionsContent key={location.key} {...props} />;
};

export default HeaderActions;
