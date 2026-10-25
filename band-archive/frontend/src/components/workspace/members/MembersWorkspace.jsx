import { useCallback, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { MemberProfileEditor, MemberProfileSummary } from '../MemberProfile.jsx';
import { ErrorState } from '../WorkspaceFeedback.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { usePagedRead } from '../usePagedRead.js';
import { genericError, requestOptions } from '../workspaceReadSupport.js';
import { RosterUnavailable } from './MemberRosterUnavailable.jsx';

export const MembersWorkspace = () => {
  const { band, api, workspaceKey } = useWorkspace();
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const activeMember = band.membership && String(band.membership.status || 'ACTIVE').toLowerCase() === 'active';
  const fetchMembers = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.members(band.id, page), requestOptions(signal));
    return { entries: response?.members, pagination: response?.pagination };
  }, [api, band.id]);
  const { loading, error, notFound, entries, pagination, refresh, loadMore } = usePagedRead('members', fetchMembers);
  const rosterUnavailable = !loading && notFound;
  if (rosterUnavailable) return <RosterUnavailable />;
  const startEditing = () => { setSaved(false); setEditing(true); };
  return <section className="workspace-page" aria-labelledby="workspace-members-title"><div className="workspace-page-heading"><div><h1 id="workspace-members-title">멤버</h1><p>밴드에서 공개한 멤버와 연습 기록입니다.</p></div>{activeMember && !editing && <button type="button" className="secondary-btn" onClick={startEditing}>내 프로필 수정</button>}</div>
    {saved && <p role="status">내 프로필을 저장했습니다.</p>}
    {editing && <MemberProfileEditor key={workspaceKey} band={band} api={api} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); setSaved(true); refresh(); }} />}
    {loading && entries.length === 0 ? <p aria-live="polite">멤버를 불러오는 중입니다…</p> : error && entries.length === 0 ? <ErrorState message={genericError} retry={refresh} /> : entries.length === 0 ? <div className="workspace-empty"><p>표시할 멤버가 없습니다.</p></div> : <div className="workspace-cards">{entries.map((member) => <article className="workspace-card member-profile-card" key={member.id}><NavLink className="workspace-card-link" to={`/bands/${encodeURIComponent(band.slug)}/members/${member.id}`}><strong>{member.name || '멤버'}{member.is_self && <small className="member-self-badge">나</small>}</strong><MemberProfileSummary member={member} compact /></NavLink>{member.is_self && member.can_edit_profile && !editing && <button type="button" className="secondary-btn" onClick={startEditing}>내 프로필 수정</button>}</article>)}</div>}
    {pagination?.has_more && <button className="secondary-btn" disabled={loading} onClick={loadMore}>{loading ? '멤버를 불러오는 중…' : '멤버 더 불러오기'}</button>}
    {error && entries.length > 0 && <ErrorState message={genericError} retry={refresh} />}
  </section>;
};
