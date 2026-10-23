import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { normalizedJoinName, validJoinName } from '../../services/joinName.js';
import MemberPositionPicker from '../workspace/MemberPositionPicker.jsx';
import './BandsPage.css';

export const BandJoinRequest = ({ band, viewer }) => {
  const { user, getAccessToken, refreshSession } = useAuth();
  const { reload } = useBands();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [name, setName] = useState('');
  const [introduction, setIntroduction] = useState('');
  const [positions, setPositions] = useState([]);
  const [customPosition, setCustomPosition] = useState('');
  const [state, setState] = useState({ busy: false, error: '', submitted: false });
  const requestRef = useRef(null);
  const guard = useRef(false);

  useEffect(() => () => requestRef.current?.abort(), []);

  const submit = async (event) => {
    event.preventDefault();
    if (!band || !user || !validJoinName(name) || Array.from(introduction.trim()).length > 300 || state.busy || guard.current) return;
    guard.current = true;
    const controller = new AbortController(); requestRef.current = controller;
    setState({ busy: true, error: '', submitted: false });
    try {
      const profile = positions.length ? { positions, ...(positions.includes('OTHER') ? { custom_position: customPosition } : {}) } : {};
      const result = await api.requestJson(`/api/v2/bands/${Number(band.id)}/join-requests`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: normalizedJoinName(name), introduction: introduction.trim(), ...profile }), signal: controller.signal });
      if (controller.signal.aborted) return;
      if (!result?.created && !result?.membership) throw new Error('invalid_join_result');
      setState({ busy: false, error: '', submitted: true });
      try { await reload(); } catch { /* The confirmed request remains visible even if the account list refresh fails. */ }
    } catch {
      if (!controller.signal.aborted) setState({ busy: false, error: '가입 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.', submitted: false });
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
      guard.current = false;
    }
  };

  if (viewer?.can_open_workspace) return <section className="band-introduction-actions"><p>이미 가입한 밴드입니다.</p><Link className="primary-btn" to={`/bands/${encodeURIComponent(band.slug)}`}>밴드 공간 열기</Link></section>;
  if (viewer?.membership_status === 'pending') return <section className="band-introduction-actions"><p role="status">가입 요청이 승인 대기 중입니다.</p></section>;
  if (state.submitted) return <section className="band-introduction-actions"><p role="status">가입 요청을 보냈습니다. 승인 대기 중입니다.</p><Link className="secondary-btn" to="/account">내 가입 상태 확인하기</Link></section>;

  return <section className="band-join-request" aria-labelledby="band-join-request-title">
    <h2 id="band-join-request-title">{band.name} 가입 요청</h2>
    <p className="bands-help">밴드 관리자에게 전달할 이름과 소개를 입력해 주세요.</p>
    {state.error && <p role="alert" className="auth-error">{state.error}</p>}
    <form className="bands-form" onSubmit={submit} aria-busy={state.busy}>
      <label htmlFor="join-name">밴드에서 사용할 이름 (필수)<input id="join-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} autoComplete="name" disabled={state.busy} aria-describedby="join-name-help" /></label>
      <p id="join-name-help" className="bands-help">승인 후 멤버 목록에 표시할 이름을 100자 이내로 입력해 주세요.</p>
      <label htmlFor="join-introduction">간단한 자기소개 (선택)<textarea id="join-introduction" value={introduction} onChange={(event) => setIntroduction(event.target.value)} disabled={state.busy} aria-describedby="join-introduction-help" /></label>
      <p id="join-introduction-help" className="bands-help">밴드 관리자에게 전할 소개를 300자 이내로 적어 주세요. 전화번호·주소 등 민감한 정보는 적지 마세요. 가입 심사에만 사용하며 승인·거절 시 삭제합니다. {Array.from(introduction.trim()).length} / 300자</p>
      <MemberPositionPicker positions={positions} customPosition={customPosition} onChange={(nextPositions, nextCustom) => { setPositions(nextPositions); setCustomPosition(nextCustom); }} disabled={state.busy} idPrefix="join-position" />
      <p className="bands-help">포지션은 선택 사항이며, 가입 후 밴드 멤버 프로필에서 본인이 변경할 수 있습니다. 멤버 명단 공개 설정에 따라 비로그인 방문자에게도 보일 수 있습니다. 재가입 시 선택하지 않으면 기존 포지션이 유지됩니다.</p>
      <div className="band-actions"><button className="primary-btn" type="submit" disabled={state.busy || !validJoinName(name) || Array.from(introduction.trim()).length > 300}>{state.busy ? '요청 중…' : '가입 요청 보내기'}</button><Link className="secondary-btn" to="/bands/discover">취소</Link></div>
    </form>
  </section>;
};

export default BandJoinRequest;
