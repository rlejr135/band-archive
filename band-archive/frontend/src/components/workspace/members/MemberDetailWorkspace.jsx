/* eslint-disable react-hooks/set-state-in-effect */
import { useCallback, useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate, useParams } from 'react-router-dom';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { MemberProfileEditor, MemberProfileSummary } from '../MemberProfile.jsx';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import { ErrorState } from '../WorkspaceFeedback.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { usePagedRead } from '../usePagedRead.js';
import { genericError, requestOptions } from '../workspaceReadSupport.js';
import { createWorkspaceRequestGate } from '../workspaceRouteState.js';
import { RosterUnavailable } from './MemberRosterUnavailable.jsx';
import { PersonalLogPlayback } from './PersonalLogPlayback.jsx';

export const MemberDetailWorkspace = () => {
  const { memberId } = useParams();
  const { workspaceKey } = useWorkspace();
  return <MemberDetailReadOnly key={`${workspaceKey}:member:${memberId}`} memberId={memberId} />;
};

const MemberDetailReadOnly = ({ memberId }) => {
  const { band, api, workspaceKey } = useWorkspace();
  const navigate = useNavigate();
  const [memberState, setMemberState] = useState({ loading: true, error: '', member: null });
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const memberGate = useRef(createWorkspaceRequestGate());
  useEffect(() => {
    const controller = new AbortController();
    const key = `${workspaceKey}:member:${memberId}`;
    const token = memberGate.current.begin(key);
    setMemberState({ loading: true, error: '', member: null });
    api.requestJson(bandWorkspacePaths.member(band.id, memberId), requestOptions(controller.signal)).then((response) => {
      if (!controller.signal.aborted && memberGate.current.accepts(key, token)) setMemberState({ loading: false, error: '', member: response?.member ?? null });
    }).catch(() => {
      if (!controller.signal.aborted && memberGate.current.accepts(key, token)) setMemberState({ loading: false, error: genericError, member: null });
    });
    return () => controller.abort();
  }, [api, band.id, memberId, workspaceKey]);
  const fetchLogs = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.memberLogs(band.id, memberId, page), requestOptions(signal));
    return { entries: response?.logs, pagination: response?.pagination };
  }, [api, band.id, memberId]);
  const logs = usePagedRead(`member-logs:${memberId}`, fetchLogs);
  const [selectedLogId, setSelectedLogId] = useState(null);
  // Refreshing a log page also renews its short-lived signed URL. Keep only
  // the id in local state so a successful refresh replaces the rendered URL,
  // rather than continuing to play the expired object from a stale snapshot.
  const selectedLog = logs.entries.find((log) => log.id === selectedLogId) ?? null;
  if (!memberState.loading && (memberState.error || !memberState.member)) return <RosterUnavailable />;
  return <section className="workspace-page" aria-labelledby="workspace-member-title">
    {memberState.loading ? <p aria-live="polite">멤버 정보를 불러오는 중입니다…</p> : <><div className="workspace-page-heading"><div><h1 id="workspace-member-title">{memberState.member.name || '멤버'}{memberState.member.is_self && <small className="member-self-badge">나</small>}</h1></div><NavLink className="secondary-btn workspace-button-link" to={`/bands/${encodeURIComponent(band.slug)}/members`}>목록으로</NavLink></div>
      <MemberProfileSummary member={memberState.member} />
      {['inactive', 'left', 'pending', 'rejected'].includes(memberState.member.membership_status) && <p className="workspace-muted" role="status">현재 밴드 멤버가 아닙니다. 이전 연습 기록은 보존됩니다.</p>}
      {memberState.member.is_self && memberState.member.can_edit_profile && !editing && <button type="button" className="secondary-btn" onClick={() => { setSaved(false); setEditing(true); }}>내 프로필 수정</button>}
      {saved && <p role="status">내 프로필을 저장했습니다.</p>}
      {editing && <MemberProfileEditor key={`${workspaceKey}:${memberId}`} band={band} api={api} expectedMemberId={memberId} onCancel={() => setEditing(false)} onSaved={(member) => { setMemberState({ loading: false, error: '', member }); setEditing(false); setSaved(true); }} />}
      <div className="detail-heading"><h2>개인 연습 기록</h2><BandDeleteAction band={band} workspaceKey={`${workspaceKey}:member:${memberId}`} canDelete={memberState.member.can_delete} label="멤버 프로필" path={bandWorkspacePaths.member(band.id, memberId)} onDeleted={() => navigate(`/bands/${encodeURIComponent(band.slug)}/members`, { replace: true })} /></div>
      {logs.loading && logs.entries.length === 0 ? <p aria-live="polite">기록을 불러오는 중입니다…</p> : logs.entries.length === 0 ? <p className="workspace-muted">표시할 개인 연습 기록이 없습니다.</p> : <div className="workspace-grid"><div className="workspace-list"><ul>{logs.entries.map((log) => <li key={log.id}><button className={selectedLog?.id === log.id ? 'workspace-row selected' : 'workspace-row'} onClick={() => setSelectedLogId(log.id)}><strong>{log.title || '개인 연습 기록'}</strong><span>{log.created_at ? new Date(log.created_at).toLocaleString() : ''}</span><small>{log.file_type === 'video' ? '영상' : '오디오'}</small></button></li>)}</ul>{logs.pagination?.has_more && <button className="secondary-btn" disabled={logs.loading} onClick={logs.loadMore}>{logs.loading ? '기록을 불러오는 중…' : '기록 더 불러오기'}</button>}</div><div className="workspace-detail">{selectedLog ? <PersonalLogPlayback log={selectedLog} workspaceKey={`${workspaceKey}:member:${memberId}`} onRefresh={logs.refresh} onDeleted={() => { setSelectedLogId(null); logs.refresh(); }} /> : <p className="workspace-placeholder">기록을 선택하면 재생할 수 있습니다.</p>}</div></div>}
      {logs.error && <ErrorState message={genericError} retry={logs.refresh} />}</>}
  </section>;
};
