import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';
import { bandRoleLabel } from '../../context/bandRole.js';
import { InviteCodeEntry } from './BandOnboarding.jsx';
import BandSwitcher from './BandSwitcher.jsx';
import AuthStatus from '../layout/AuthStatus.jsx';
import './BandsPage.css';

const profileName = (user) => {
  const metadata = user?.user_metadata || {};
  return [metadata.display_name, metadata.full_name, metadata.name].find((value) => typeof value === 'string' && value.trim())?.trim() || '';
};
const profileEmail = (user) => typeof user?.email === 'string' ? user.email : '';

export const BandsPage = () => {
  const { user } = useAuth();
  const { activeBands, pendingMemberships, selectedBand, selectBand, loading, error, reload } = useBands();
  const navigate = useNavigate();
  const name = profileName(user);
  const email = profileEmail(user);

  const openBand = (band) => {
    if (!band?.slug) return;
    selectBand(band.id);
    navigate(`/bands/${encodeURIComponent(band.slug)}`);
  };

  return <div className="bands-shell">
    <header className="bands-header">
      <Link to="/bands" className="bands-brand">모두의 Band Archive</Link>
      <div className="bands-header-actions"><BandSwitcher accountMode /><AuthStatus /></div>
    </header>
    <main className="account-hub" aria-labelledby="account-hub-title">
      <div className="account-heading">
        <div><p className="account-eyebrow">YOUR ACCOUNT</p><h1 id="account-hub-title">계정과 밴드 관리</h1><p>프로필을 확인하고 작업할 밴드 공간을 선택하세요.</p></div>
        <span className="account-avatar" aria-hidden="true">{(name || email || 'B').trim().slice(0, 1).toUpperCase()}</span>
      </div>

      <section className="account-profile" aria-labelledby="profile-heading">
        <div className="account-section-heading"><div><p className="account-eyebrow">PROFILE</p><h2 id="profile-heading">계정 정보</h2></div><Link to="/forgot-password">비밀번호 재설정</Link></div>
        <dl className="profile-details">
          {name && <div><dt>표시 이름</dt><dd>{name}</dd></div>}
          <div><dt>이메일</dt><dd>{email || '이메일 정보 없음'}</dd></div>
        </dl>
        <p className="account-readonly-note">계정 정보는 인증 서비스에 등록된 값이며 이 화면에서는 변경되지 않습니다.</p>
      </section>

      <section className="account-memberships" aria-labelledby="memberships-heading">
        <div className="account-section-heading"><div><p className="account-eyebrow">WORKSPACES</p><h2 id="memberships-heading">내 밴드</h2></div><div className="account-section-actions"><button type="button" className="secondary-btn" onClick={() => navigate('/bands/discover')}>공개 밴드 찾기</button><button type="button" className="primary-btn" onClick={() => navigate('/bands/request')}>밴드 생성 요청</button></div></div>
        {loading ? <p aria-live="polite">밴드 정보를 불러오는 중입니다…</p> : error ? <div className="account-error" role="alert"><span>{error}</span><button type="button" className="secondary-btn" onClick={reload}>다시 시도</button></div> : activeBands.length === 0 ? <div className="account-empty"><h3>아직 가입한 밴드가 없습니다</h3><p>공개 밴드를 둘러보거나 초대 코드를 입력해 참여할 수 있어요.</p><button type="button" className="secondary-btn" onClick={() => document.getElementById('invite-entry-title')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>초대 코드 입력하기</button></div> : <ul className="account-band-list">
          {activeBands.map((band) => <li className="account-band-card" key={band.id}>
            <button type="button" className={`account-band-open${selectedBand?.id === band.id ? ' is-selected' : ''}`} onClick={() => openBand(band)} aria-pressed={selectedBand?.id === band.id}>
              <span className="account-band-mark" aria-hidden="true">♫</span>
              <span className="account-band-copy"><strong>{band.name}</strong><small>{band.slug}</small></span>
              <span className="account-role">{bandRoleLabel(band.membership?.role)}</span>
              <span className="account-band-arrow" aria-hidden="true">↗</span>
            </button>
            {band.permissions?.can_manage_members && band.permissions?.can_manage_invites && <Link className="account-manage-link" to={`/bands/${encodeURIComponent(band.slug)}/manage`}>멤버와 초대 관리</Link>}
          </li>)}
        </ul>}
        {pendingMemberships.length > 0 && <section className="account-pending" aria-labelledby="pending-bands-heading"><h3 id="pending-bands-heading">가입 대기</h3><ul>{pendingMemberships.map((membership) => <li key={membership.id}><span>{membership.band?.name || '밴드'}</span><span>승인 대기 중</span></li>)}</ul></section>}
        <InviteCodeEntry onJoined={reload} />
      </section>
    </main>
  </div>;
};
