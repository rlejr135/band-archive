import { useCallback, useRef, useState } from 'react';
import { useServicePolicy } from '../../../context/ServicePolicyContext.jsx';
import { reviewPublicVisibility } from '../../../services/uploadPolicy.js';
import { bandWorkspacePaths, suggestionPayload, votePayload } from '../../../services/bandWorkspaceApi.js';
import { safeExternalHttpUrl } from '../../../services/safeExternalUrl.js';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import { ErrorState } from '../WorkspaceFeedback.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { usePagedRead } from '../usePagedRead.js';
import { WriteGuidance } from '../WorkspaceWriteGuidance.jsx';
import { genericError, requestOptions } from '../workspaceReadSupport.js';
import '../BandWorkspace.css';

export const SuggestionsWorkspace = () => {
  const { band, api, workspaceKey } = useWorkspace();
  const fetchSuggestions = useCallback(async (page, signal) => {
    const response = await api.requestJson(bandWorkspacePaths.suggestions(band.id, page), requestOptions(signal));
    return { entries: response?.suggestions, pagination: response?.pagination };
  }, [api, band.id]);
  const suggestions = usePagedRead('suggestions', fetchSuggestions);
  const member = Boolean(band.membership && String(band.membership.status || 'ACTIVE').toLowerCase() === 'active');
  const suggestionCards = suggestions.entries.map((suggestion) => <SuggestionCard key={suggestion.id} suggestion={suggestion} member={member} onChanged={suggestions.refresh} />);
  const [creating, setCreating] = useState(false);
  return <section className="workspace-page workspace-suggestions-page" aria-labelledby="workspace-suggestions-title"><div className="workspace-page-heading"><div><h1 id="workspace-suggestions-title">추천곡</h1><p>{member ? '추천곡을 등록하고 투표해 보세요.' : '공개된 추천곡을 확인할 수 있습니다.'}</p></div>{member && <button type="button" className="primary-btn workspace-suggestions-add" onClick={() => setCreating(true)}>추천곡 추가</button>}</div>
    {!member && <WriteGuidance />}{creating && <SuggestionForm key={workspaceKey} onDone={() => { setCreating(false); suggestions.refresh(); }} onCancel={() => setCreating(false)} />}
    {suggestions.loading && suggestions.entries.length === 0 ? <p aria-live="polite">추천곡을 불러오는 중입니다…</p> : suggestions.error && suggestions.entries.length === 0 ? <ErrorState message={genericError} retry={suggestions.refresh} /> : suggestions.entries.length === 0 ? <div className="workspace-empty"><p>표시할 추천곡이 없습니다.</p>{member && <button type="button" className="secondary-btn" onClick={() => setCreating(true)}>첫 추천곡 추가</button>}</div> : <div className="workspace-cards workspace-cards--suggestions">{suggestionCards}</div>}
    {suggestions.pagination?.has_more && <button className="secondary-btn" disabled={suggestions.loading} onClick={suggestions.loadMore}>{suggestions.loading ? '추천곡을 불러오는 중…' : '추천곡 더 불러오기'}</button>}
    {suggestions.error && suggestions.entries.length > 0 && <ErrorState message={genericError} retry={suggestions.refresh} />}
  </section>;
};

const SuggestionForm = ({ item, onDone, onCancel }) => {
  const { config } = useServicePolicy();
  const { band, api } = useWorkspace(); const [value, setValue] = useState({ title: item?.title || '', artist: item?.artist || '', link: item?.link || '', memo: item?.memo || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS' }); const [saving, setSaving] = useState(false); const [error, setError] = useState(''); const flight = useRef(false);
  const save = async (event) => { event.preventDefault(); if (flight.current) return; const decision = reviewPublicVisibility(config, { previous: item?.visibility, next: value.visibility }); if (!decision.proceed) { if (decision.error) setError(decision.error); return; } flight.current = true; setSaving(true); setError(''); try { await api.requestJson(item ? bandWorkspacePaths.suggestion(band.id, item.id) : bandWorkspacePaths.suggestions(band.id), { method: item ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...suggestionPayload(value), ...(decision.confirmation ? { visibility_confirm: decision.confirmation } : {}) }) }); onDone(); } catch { setError('추천곡을 저장하지 못했습니다. 입력값을 확인해 주세요.'); setSaving(false); } finally { flight.current = false; } };
  const change = (event) => setValue((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  return <form className="workspace-form" onSubmit={save}><h2>{item ? '추천곡 수정' : '추천곡 추가'}</h2><label>제목<input required name="title" value={value.title} onChange={change} /></label><label>아티스트<input required name="artist" value={value.artist} onChange={change} /></label><label>참고 링크<input required type="url" name="link" value={value.link} onChange={change} /></label><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label><label>공개 범위<select name="visibility" value={value.visibility} onChange={change}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={onCancel}>취소</button><button className="primary-btn" disabled={saving}>{saving ? '저장 중…' : '저장'}</button></div></form>;
};

const SuggestionCard = ({ suggestion, member, onChanged }) => {
  const { band, api } = useWorkspace(); const [editing, setEditing] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const voteFlight = useRef(false); const writeFlight = useRef(false);
  const safeLink = safeExternalHttpUrl(suggestion.link);
  const canManage = suggestion.can_edit || suggestion.can_delete || suggestion.can_proceed;
  const vote = async (clicked) => { if (!member || voteFlight.current) return; voteFlight.current = true; setBusy(true); setError(''); try { await api.requestJson(bandWorkspacePaths.suggestionVote(band.id, suggestion.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(votePayload(suggestion.viewer_vote === clicked ? 0 : clicked)) }); onChanged(); } catch { setError('투표를 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.'); } finally { voteFlight.current = false; setBusy(false); } };
  const proceed = async () => { if (!suggestion.can_proceed || writeFlight.current || !window.confirm('이 추천곡을 곡 목록으로 진행할까요? 이 작업은 추천글을 목록에서 제거합니다.')) return; writeFlight.current = true; setBusy(true); setError(''); try { await api.requestJson(bandWorkspacePaths.suggestionProceed(band.id, suggestion.id), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ visibility: suggestion.visibility }) }); onChanged(); } catch { setError('추천곡 진행을 완료하지 못했습니다.'); } finally { writeFlight.current = false; setBusy(false); } };
  if (editing) return <SuggestionForm item={suggestion} onDone={() => { setEditing(false); onChanged(); }} onCancel={() => setEditing(false)} />;
  return <article className="workspace-card suggestion-card">
    <header className="suggestion-card-heading">
      <div className="suggestion-card-title-block">
        <h2 className="suggestion-card-title">{suggestion.title || '제목 없음'}</h2>
        <p className="suggestion-card-artist">{suggestion.artist || '아티스트 정보 없음'}</p>
      </div>
      <span className="suggestion-status">{suggestion.status === 'pending' ? '검토 대기' : suggestion.status}</span>
    </header>
    {suggestion.memo && <p className="suggestion-card-memo">{suggestion.memo}</p>}
    <div className="suggestion-card-meta"><span aria-live="polite">점수 {suggestion.vote_score ?? 0}</span></div>
    {safeLink && <a className="suggestion-card-link" href={safeLink} target="_blank" rel="noopener noreferrer">참고 링크 열기</a>}
    {member ? <div className="suggestion-actions">
      <div className="suggestion-vote-actions" role="group" aria-label="추천곡 투표">
        <button type="button" className="secondary-btn suggestion-vote-button suggestion-vote-button--positive" aria-pressed={suggestion.viewer_vote === 1} disabled={busy} onClick={() => vote(1)}>추천</button>
        <button type="button" className="secondary-btn suggestion-vote-button suggestion-vote-button--negative" aria-pressed={suggestion.viewer_vote === -1} disabled={busy} onClick={() => vote(-1)}>아쉬워요</button>
      </div>
      {canManage && <div className="suggestion-manage-actions" role="group" aria-label="추천곡 관리">
        {suggestion.can_edit && <button type="button" className="secondary-btn" disabled={busy} onClick={() => setEditing(true)}>수정</button>}
        <BandDeleteAction band={band} workspaceKey={`suggestion:${suggestion.id}`} canDelete={suggestion.can_delete} label="추천글" path={bandWorkspacePaths.suggestion(band.id, suggestion.id)} onDeleted={onChanged} />
        {suggestion.can_proceed && <button type="button" className="primary-btn" disabled={busy} onClick={proceed}>곡으로 진행</button>}
      </div>}
    </div> : <p className="workspace-muted">로그인 후 밴드에 가입하면 투표와 추천이 가능합니다.</p>}
    {error && <p className="workspace-error" role="alert">{error}</p>}
  </article>;
};
