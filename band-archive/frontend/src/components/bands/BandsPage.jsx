import { Link, useNavigate } from 'react-router-dom';
import { useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';
import { bandRoleLabel } from '../../context/bandRole.js';
import { UploadContext } from '../../context/UploadContext.jsx';
import { isUploadInProgress } from '../../services/uploadPresentation.js';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { memberPositionLabels } from '../workspace/memberPositions.js';
import { InviteLinkGuidance } from './BandOnboarding.jsx';
import BandSwitcher from './BandSwitcher.jsx';
import AuthStatus from '../layout/AuthStatus.jsx';
import './BandsPage.css';
import { usePlatformAdmin } from './usePlatformAdmin.js';

const profileName = (user) => {
  const metadata = user?.user_metadata || {};
  return [metadata.display_name, metadata.full_name, metadata.name].find((value) => typeof value === 'string' && value.trim())?.trim() || '';
};
const profileEmail = (user) => typeof user?.email === 'string' ? user.email : '';
const byteUploadStatuses = new Set(['preparing', 'queued', 'uploading', 'retry_wait', 'completing', 'auth_required']);
const retainedContentNotice = '탈퇴해도 내가 작성한 곡, 합주, 사진, 개인 연습 기록·첨부, 추천글과 댓글은 밴드에 남습니다. 탈퇴 후에는 작성자 본인도 삭제할 수 없습니다.';

const BandMembershipCard = ({ band, userId, selected, onOpen, onLeave }) => {
  const { getAccessToken, refreshSession } = useAuth();
  const { applyBandLeave, refreshBandMembership } = useBands();
  const uploadQueue = useContext(UploadContext);
  const tasks = uploadQueue?.tasks || [];
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [leaveUi, setLeaveUi] = useState({ mode: 'idle', message: '', targets: [], targetId: '' });
  const operationRef = useRef(null);
  const inFlight = useRef(false);
  const alive = useRef(true);
  const identity = `${userId}:${band.id}`;
  const owner = band.membership?.role === 'owner';
  const canLeave = band.permissions?.can_leave === true;
  const canTransfer = band.permissions?.can_transfer_ownership === true;
  const activeOwnUploads = tasks.filter((task) => Number(task.bandId) === Number(band.id) && byteUploadStatuses.has(task.status) && isUploadInProgress(task.status));

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; operationRef.current?.abort(); };
  }, [identity]);

  const isCurrent = (controller, capturedIdentity) => alive.current && !controller.signal.aborted && capturedIdentity === identity;
  const requestLeave = async (controller, capturedIdentity, transferred = false) => {
    try {
      await api.requestJson(`/api/v2/bands/${Number(band.id)}/leave`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}), signal: controller.signal });
      if (!isCurrent(controller, capturedIdentity)) return;
      applyBandLeave(band.id, band.slug);
      onLeave();
    } catch (error) {
      if (!isCurrent(controller, capturedIdentity)) return;
        const message = error?.status === 409
          ? (transferred ? '소유권 이전은 완료됐지만 서버가 탈퇴를 보류했습니다. 새 소유권은 유지됩니다. 업로드 또는 멤버십 상태를 확인한 뒤 다시 시도해 주세요.' : '서버가 탈퇴를 보류했습니다. 업로드가 진행 중이거나 소유권·멤버십 상태가 바뀌었을 수 있습니다. 밴드 권한과 업로드함을 확인한 뒤 다시 시도해 주세요.')
        : (transferred ? '소유권 이전은 완료됐습니다. 밴드의 새 권한을 확인한 뒤 탈퇴를 다시 시도해 주세요.' : '밴드 탈퇴를 처리하지 못했습니다. 권한과 연결 상태를 확인하고 다시 시도해 주세요.');
      setLeaveUi((previous) => ({ ...previous, mode: transferred ? 'transferred' : 'idle', message }));
    } finally { if (isCurrent(controller, capturedIdentity)) inFlight.current = false; }
  };

  const loadTargets = async () => {
    if (inFlight.current) return;
    const controller = new AbortController(); operationRef.current?.abort(); operationRef.current = controller; inFlight.current = true;
    const capturedIdentity = identity;
    setLeaveUi({ mode: 'loading-targets', message: '', targets: [], targetId: '' });
    try {
      const response = await api.requestJson(`/api/v2/bands/${Number(band.id)}/memberships`, { signal: controller.signal });
      if (!isCurrent(controller, capturedIdentity)) return;
      const targets = (Array.isArray(response?.memberships) ? response.memberships : []).filter((membership) => membership?.status === 'active' && membership.can_receive_ownership === true && String(membership.id) !== String(band.membership?.id) && (membership.user?.id == null || String(membership.user.id) !== String(userId)));
      setLeaveUi({ mode: 'choose-owner', message: targets.length ? '' : '소유권을 이전할 수 있는 다른 활성 멤버가 없습니다. 마지막 owner는 탈퇴할 수 없습니다.', targets, targetId: '' });
    } catch {
      if (isCurrent(controller, capturedIdentity)) setLeaveUi({ mode: 'idle', message: '멤버 목록을 확인하지 못했습니다. 다시 시도해 주세요.', targets: [], targetId: '' });
    } finally { if (isCurrent(controller, capturedIdentity)) inFlight.current = false; }
  };

  const transferAndLeave = async () => {
    if (inFlight.current || leaveUi.mode !== 'choose-owner' || !leaveUi.targetId) return;
    if (!window.confirm(`선택한 멤버에게 소유권을 이전한 뒤 탈퇴합니다. ${retainedContentNotice}`)) return;
    const controller = new AbortController(); operationRef.current?.abort(); operationRef.current = controller; inFlight.current = true;
    const capturedIdentity = identity;
    let ownershipTransferred = false;
    setLeaveUi((previous) => ({ ...previous, mode: 'transferring', message: '' }));
    try {
      await api.requestJson(`/api/v2/bands/${Number(band.id)}/ownership-transfer`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ membership_id: Number(leaveUi.targetId) }), signal: controller.signal });
      if (!isCurrent(controller, capturedIdentity)) return;
      ownershipTransferred = true;
      setLeaveUi((previous) => ({ ...previous, mode: 'transferred', message: '소유권 이전이 완료되었습니다. 탈퇴를 이어서 처리합니다.' }));
      const refreshedBand = await refreshBandMembership(band.id, band.slug, controller.signal);
      if (!isCurrent(controller, capturedIdentity) || !refreshedBand || refreshedBand.permissions?.can_leave !== true) {
        setLeaveUi((previous) => ({ ...previous, mode: 'transferred', message: '소유권 이전은 완료됐지만 새 권한을 다시 확인하지 못했습니다. 소유권은 되돌리지 않았습니다. 밴드 목록을 새로고침한 뒤 탈퇴를 다시 시도해 주세요.' }));
        return;
      }
      await requestLeave(controller, capturedIdentity, true);
    } catch {
      if (isCurrent(controller, capturedIdentity)) setLeaveUi((previous) => ({ ...previous, mode: ownershipTransferred ? 'transferred' : 'choose-owner', message: ownershipTransferred ? '소유권 이전은 완료됐지만 현재 권한을 다시 확인하지 못했습니다. 소유권은 되돌리지 않았습니다. 밴드 목록을 새로고침한 뒤 탈퇴를 다시 시도해 주세요.' : '소유권 이전을 완료하지 못했습니다. 대상 멤버가 아직 이전 가능한지 확인한 뒤 다시 시도해 주세요.' }));
    } finally { if (isCurrent(controller, capturedIdentity)) inFlight.current = false; }
  };

  const leaveNow = async () => {
    if (inFlight.current) return;
    if (!window.confirm(retainedContentNotice)) return;
    const controller = new AbortController(); operationRef.current?.abort(); operationRef.current = controller; inFlight.current = true;
    setLeaveUi({ mode: 'leaving', message: '', targets: [], targetId: '' });
    await requestLeave(controller, identity);
  };
  const retryLeave = async () => {
    if (inFlight.current || leaveUi.mode !== 'transferred') return;
    if (activeOwnUploads.length > 0) {
      setLeaveUi((previous) => ({ ...previous, message: '이 밴드의 업로드가 끝나거나 취소될 때까지 탈퇴를 보류합니다. 밴드 공간의 업로드함에서 상태를 확인해 주세요.' }));
      return;
    }
    const controller = new AbortController(); operationRef.current?.abort(); operationRef.current = controller; inFlight.current = true;
    setLeaveUi((previous) => ({ ...previous, mode: 'leaving', message: '' }));
    try {
      const refreshedBand = await refreshBandMembership(band.id, band.slug, controller.signal);
      if (!isCurrent(controller, identity)) return;
      if (!refreshedBand || refreshedBand.permissions?.can_leave !== true) {
        setLeaveUi((previous) => ({ ...previous, mode: 'transferred', message: '소유권 이전은 완료됐지만 현재 권한을 확인하지 못했습니다. 밴드 목록을 새로고침한 뒤 탈퇴를 다시 시도해 주세요.' }));
        return;
      }
      await requestLeave(controller, identity, true);
    } catch {
      if (isCurrent(controller, identity)) setLeaveUi((previous) => ({ ...previous, mode: 'transferred', message: '소유권 이전은 완료됐지만 현재 권한을 확인하지 못했습니다. 밴드 목록을 새로고침한 뒤 탈퇴를 다시 시도해 주세요.' }));
    } finally { if (isCurrent(controller, identity)) inFlight.current = false; }
  };

  return <li className="account-band-card" data-band-id={band.id}>
    <button type="button" className={`account-band-open${selected ? ' is-selected' : ''}`} onClick={() => onOpen(band)} aria-pressed={selected}>
      <span className="account-band-mark" aria-hidden="true">♫</span>
      <span className="account-band-copy"><strong>{band.name}</strong><small>{band.slug}</small></span>
      <span className="account-role">{bandRoleLabel(band.membership?.role)}</span>
      <span className="account-band-arrow" aria-hidden="true">↗</span>
    </button>
    {band.permissions?.can_manage_members && band.permissions?.can_manage_invites && <Link className="account-manage-link" to={`/bands/${encodeURIComponent(band.slug)}/manage`}>멤버와 초대 관리</Link>}
    {(canLeave || canTransfer) && <div className="account-band-leave">
      {leaveUi.mode === 'idle' && <button type="button" className="secondary-btn" disabled={activeOwnUploads.length > 0} onClick={owner ? loadTargets : leaveNow}>{owner ? '소유권 이전 후 탈퇴' : '밴드 탈퇴'}</button>}
      {leaveUi.mode === 'loading-targets' && <p aria-live="polite">이전 가능한 멤버를 확인하는 중…</p>}
      {leaveUi.mode === 'choose-owner' && <div className="bands-form"><label>소유권을 이전할 멤버<select aria-label={`${band.name} 소유권 이전 대상`} value={leaveUi.targetId} onChange={(event) => setLeaveUi((previous) => ({ ...previous, targetId: event.target.value }))}><option value="">멤버 선택</option>{leaveUi.targets.map((target) => <option key={target.id} value={target.id}>{target.user?.display_name || target.user?.email || '활성 멤버'}</option>)}</select></label><p className="bands-help">이전 후에도 작성 콘텐츠는 밴드에 남습니다. 이전이 완료된 뒤 탈퇴를 처리합니다.</p><div className="band-actions"><button type="button" className="primary-btn" disabled={!leaveUi.targetId || inFlight.current || activeOwnUploads.length > 0} onClick={transferAndLeave}>이전하고 탈퇴</button><button type="button" className="secondary-btn" disabled={inFlight.current} onClick={() => setLeaveUi({ mode: 'idle', message: '', targets: [], targetId: '' })}>취소</button></div></div>}
      {['transferring', 'leaving'].includes(leaveUi.mode) && <p aria-live="polite">{leaveUi.mode === 'transferring' ? '소유권 이전 중…' : '탈퇴 처리 중…'}</p>}
      {leaveUi.mode === 'transferred' && <div><p className="account-error" role="status">{leaveUi.message}</p><button type="button" className="secondary-btn" onClick={retryLeave}>탈퇴 다시 시도</button></div>}
      {leaveUi.message && leaveUi.mode !== 'transferred' && <p className="account-error" role="alert">{leaveUi.message}</p>}
      {activeOwnUploads.length > 0 && <p className="bands-help">이 밴드의 업로드 {activeOwnUploads.length}개가 진행 중이라 현재 탭에서 탈퇴를 막았습니다. <Link to={`/bands/${encodeURIComponent(band.slug)}`}>밴드 공간</Link>의 업로드함에서 완료하거나 취소한 뒤 다시 시도하세요. 다른 기기의 업로드는 서버가 별도로 확인합니다.</p>}
    </div>}
    {!canLeave && owner && band.permissions?.can_transfer_ownership !== true && <p className="bands-help">소유권을 이전한 뒤 탈퇴할 수 있습니다. 혼자 남은 owner는 탈퇴할 수 없습니다.</p>}
  </li>;
};

export const BandsPage = () => {
  const { user } = useAuth();
  const { activeBands, pendingMemberships, selectedBand, selectBand, loading, error, reload } = useBands();
  const navigate = useNavigate();
  const name = profileName(user);
  const email = profileEmail(user);
  const { admin: isPlatformAdmin } = usePlatformAdmin();

  const openBand = (band) => {
    if (!band?.slug) return;
    selectBand(band.id);
    navigate(`/bands/${encodeURIComponent(band.slug)}`);
  };

  return <div className="bands-shell">
    <header className="bands-header">
      <Link to="/portal" className="bands-brand"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M8 24V8h8a5 5 0 0 1 3.5 8.6A5 5 0 0 1 17 26H8m5-13h3a2 2 0 1 0 0-4h-3m0 12h4a2 2 0 1 0 0-4h-4" /></svg></span>Band Archive</Link>
      <div className="bands-header-actions"><BandSwitcher accountMode /><Link className="account-settings-link" to="/account" aria-label="계정 설정" title="계정 설정"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm8 3.5a8.1 8.1 0 0 0-.1-1.3l1.5-1.2-1.5-2.6-1.8.7a7.9 7.9 0 0 0-2.2-1.3L15.6 4h-3.1l-.3 1.9A7.9 7.9 0 0 0 10 7.2l-1.8-.7-1.5 2.6 1.5 1.2a8.1 8.1 0 0 0 0 2.6l-1.5 1.2 1.5 2.6 1.8-.7a7.9 7.9 0 0 0 2.2 1.3l.3 1.9h3.1l.3-1.9a7.9 7.9 0 0 0 2.2-1.3l1.8.7 1.5-2.6-1.5-1.2c.1-.4.1-.8.1-1.2Z" /></svg></Link><AuthStatus /></div>
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
        <div className="account-section-heading"><div><p className="account-eyebrow">WORKSPACES</p><h2 id="memberships-heading">내 밴드</h2>{isPlatformAdmin && <span className="account-role">Superadmin</span>}</div><div className="account-section-actions">{isPlatformAdmin && <Link className="secondary-btn" to="/platform/band-requests">밴드 생성 요청 관리</Link>}<button type="button" className="secondary-btn" onClick={() => navigate('/bands/discover')}>공개 밴드 찾기</button><button type="button" className="primary-btn" onClick={() => navigate('/bands/request')}>밴드 생성 요청</button></div></div>
        {loading ? <p aria-live="polite">밴드 정보를 불러오는 중입니다…</p> : error ? <div className="account-error" role="alert"><span>{error}</span><button type="button" className="secondary-btn" onClick={reload}>다시 시도</button></div> : activeBands.length === 0 ? <div className="account-empty"><h3>아직 가입한 밴드가 없습니다</h3><p>공개 밴드를 둘러보거나 밴드 관리자에게 초대 링크를 요청해 주세요.</p></div> : <ul className="account-band-list">
          {activeBands.map((band) => <BandMembershipCard key={`${user?.id}:${band.id}`} band={band} userId={user?.id} selected={selectedBand?.id === band.id} onOpen={openBand} onLeave={() => {}} />)}
        </ul>}
        {pendingMemberships.length > 0 && <section className="account-pending" aria-labelledby="pending-bands-heading"><h3 id="pending-bands-heading">가입 대기</h3><ul>{pendingMemberships.map((membership) => <li key={membership.id}><span>{membership.band?.name || '밴드'}{Array.isArray(membership.requested_positions) && membership.requested_positions.length > 0 && <small className="account-pending-positions">요청 포지션: {memberPositionLabels(membership.requested_positions, membership.requested_custom_position).join(' · ') || '미설정'}</small>}</span><span>승인 대기 중</span></li>)}</ul></section>}
        <InviteLinkGuidance />
      </section>
    </main>
  </div>;
};
