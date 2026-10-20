import { useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { membershipName } from '../../services/memberManagement.js';
import './MemberRemoval.css';

// The API, not role labels or a client's account metadata, decides each action.
export const MemberRemovalButton = ({ api, band, membership, onRemoved }) => {
  const { user, authGeneration = 0 } = useAuth();
  const identity = `${user?.id || 'anonymous'}:${authGeneration}:${band.id}:${membership.id}`;
  return <RemovalAction key={identity} api={api} band={band} membership={membership} onRemoved={onRemoved} />;
};

const RemovalAction = ({ api, band, membership, onRemoved }) => {
  const [state, setState] = useState({ busy: false, completed: false, blocked: false, error: '', warning: '' });
  const controller = useRef(null); const busy = useRef(false); const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; controller.current?.abort(); }; }, []);
  if (membership.can_remove_from_band !== true) return null;
  const remove = async () => {
    if (busy.current || state.completed || state.blocked || !active.current) return;
    if (!window.confirm(`“${band.name}” 밴드에서 “${membershipName(membership)}” 님을 추방할까요?\n\n밴드 접근 권한만 해제합니다. 작성한 콘텐츠와 멤버 프로필은 보존되며, 추후 다시 가입할 수 있습니다.`)) return;
    busy.current = true; const request = new AbortController(); controller.current = request;
    setState({ busy: true, completed: false, blocked: false, error: '', warning: '' });
    try {
      const result = await api.requestJson(`/api/v2/bands/${Number(band.id)}/memberships/${Number(membership.id)}/remove`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: true }), signal: request.signal,
      });
      if (Number(result?.membership?.id) !== Number(membership.id) || Number(result?.membership?.band_id) !== Number(band.id) || result?.membership?.status !== 'left' || typeof result?.changed !== 'boolean') throw new Error('invalid_removal_result');
    } catch (error) {
      if (active.current && !request.signal.aborted) setState({ busy: false, completed: false, blocked: true, warning: '', error: [401, 403].includes(error?.status) ? '추방 권한을 확인할 수 없습니다. 권한을 다시 확인해 주세요.' : error?.status === 409 ? '본인 또는 현재 소유자는 추방할 수 없습니다. 명단을 새로 확인해 주세요.' : '추방 요청 결과를 확인하지 못했습니다. 중복 요청하지 말고 먼저 명단을 새로 확인해 주세요.' });
      busy.current = false; return;
    }
    if (!active.current || request.signal.aborted) return;
    // A committed removal remains successful even if the subsequent roster read fails.
    setState({ busy: false, completed: true, error: '', warning: '' });
    try { await onRemoved(membership.id); }
    catch { if (active.current && !request.signal.aborted) setState({ busy: false, completed: true, error: '', warning: '추방은 완료됐지만 최신 명단을 불러오지 못했습니다. 명단을 새로고침해 주세요.' }); }
  };
  return <div className="member-removal-action">
    <button type="button" className="secondary-btn member-remove-btn" disabled={state.busy || state.completed || state.blocked} onClick={remove} aria-label={`${membershipName(membership)} 추방`}>{state.completed ? '추방 완료' : state.blocked ? '명단 재확인 필요' : state.busy ? '추방 중…' : '추방'}</button>
    {state.error && <p className="auth-error" role="alert">{state.error}</p>}
    {state.warning && <p role="status">{state.warning}</p>}
  </div>;
};

export const PlatformMemberManagement = ({ allowed }) => {
  const { user, authGeneration = 0, getAccessToken, refreshSession } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  if (!allowed) return null;
  return <PlatformMemberForm key={`${user?.id || 'anonymous'}:${authGeneration}`} api={api} />;
};

const PlatformMemberForm = ({ api }) => {
  const [bandId, setBandId] = useState(''); const [selection, setSelection] = useState({ id: null, revision: 0 });
  const valid = /^\d+$/.test(bandId) && Number.isSafeInteger(Number(bandId)) && Number(bandId) > 0;
  return <section className="platform-member-panel" aria-labelledby="platform-members-title">
    <h2 id="platform-members-title">밴드 멤버 관리</h2><p>소속 여부와 무관하게 밴드 ID로 활성 멤버를 확인하고 추방할 수 있습니다. 본인과 현재 소유자는 추방할 수 없습니다.</p>
    <form className="bands-form" onSubmit={(event) => { event.preventDefault(); if (valid) setSelection((previous) => ({ id: Number(bandId), revision: previous.revision + 1 })); }}><label>관리할 밴드 ID<input inputMode="numeric" value={bandId} onChange={(event) => { setBandId(event.target.value); setSelection((previous) => ({ ...previous, id: null })); }} required /></label><button className="secondary-btn" type="submit" disabled={!valid}>멤버 명단 조회</button></form>
    {selection.id && <><button type="button" className="secondary-btn" onClick={() => setSelection((previous) => ({ ...previous, revision: previous.revision + 1 }))}>명단 새로고침</button><PlatformRoster key={`${selection.id}:${selection.revision}`} api={api} bandId={selection.id} /></>}
  </section>;
};

const PlatformRoster = ({ api, bandId }) => {
  const [state, setState] = useState({ loading: true, band: null, memberships: [], error: '', message: '' });
  const active = useRef(true);
  useEffect(() => {
    active.current = true; const request = new AbortController();
    api.requestJson(`/api/v2/platform/bands/${bandId}/memberships`, { signal: request.signal }).then((result) => {
      if (!active.current || request.signal.aborted) return;
      const validId = (value) => Number.isSafeInteger(value) && value > 0;
      if (result?.band?.id !== bandId || typeof result?.band?.name !== 'string' || !Array.isArray(result?.memberships) || result.memberships.some((item) => !validId(item?.id) || item.band_id !== bandId || item.status !== 'active' || !['owner', 'admin', 'member'].includes(item.role) || typeof item.member_name !== 'string' || typeof item.can_remove_from_band !== 'boolean') || new Set(result.memberships.map((item) => item.id)).size !== result.memberships.length) throw new Error('invalid_roster');
      setState({ loading: false, band: result.band, memberships: result.memberships.filter((item) => item.status === 'active'), error: '', message: '' });
    }).catch(() => { if (active.current && !request.signal.aborted) setState({ loading: false, band: null, memberships: [], message: '', error: '멤버 명단을 불러오지 못했습니다. 밴드 ID와 관리자 권한을 확인해 주세요.' }); });
    return () => { active.current = false; request.abort(); };
  }, [api, bandId]);
  const removed = (id) => { if (active.current) setState((previous) => ({ ...previous, memberships: previous.memberships.filter((item) => item.id !== id), message: '멤버를 추방했습니다. 기존 콘텐츠와 프로필은 보존됩니다.' })); };
  return <div aria-busy={state.loading}>{state.loading && <p role="status">멤버 명단을 불러오는 중입니다…</p>}{state.error && <p role="alert">{state.error}</p>}{state.message && <p role="status">{state.message}</p>}{state.band && <><h3>{state.band.name} · ID {state.band.id}</h3><ul className="band-list">{state.memberships.map((item) => <li key={item.id}><span>{membershipName(item)} · {item.role}</span><MemberRemovalButton api={api} band={state.band} membership={item} onRemoved={removed} /></li>)}</ul>{state.memberships.length === 0 && <p>활성 멤버가 없습니다.</p>}</>}</div>;
};
