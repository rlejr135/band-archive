import { isActiveBandMembership } from '../../../services/bandMembership.js';
import { useCallback, useRef, useState } from 'react';
import { useServicePolicy } from '../../../context/ServicePolicyContext.jsx';
import { reviewPublicVisibility } from '../../../services/uploadPolicy.js';
import { bandWorkspacePaths, suggestionPayload, votePayload } from '../../../services/bandWorkspaceApi.js';
import { safeExternalHttpUrl } from '../../../services/safeExternalUrl.js';
import { youtubeVideoId } from '../../../services/youtubeVideoId.js';
import { SuggestionVideo } from './SuggestionVideo.jsx';
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
  const member = isActiveBandMembership(band.membership);
  const suggestionCards = suggestions.entries.map((suggestion) => <SuggestionCard key={suggestion.id} suggestion={suggestion} member={member} onChanged={suggestions.refresh} />);
  const [creating, setCreating] = useState(false);
  return <section className="workspace-page workspace-suggestions-page" aria-labelledby="workspace-suggestions-title"><div className="workspace-page-heading"><div><h1 id="workspace-suggestions-title">추천곡</h1><p>{member ? '추천곡을 등록하고 좋아요를 눌러 보세요.' : '공개된 추천곡을 확인할 수 있습니다.'}</p></div>{member && <button type="button" className="primary-btn workspace-suggestions-add" onClick={() => setCreating(true)}>추천곡 추가</button>}</div>
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
  const videoId = youtubeVideoId(safeLink);
  const liked = suggestion.viewer_vote === 1;
  const toggleLike = async () => { if (!member || voteFlight.current) return; voteFlight.current = true; setBusy(true); setError(''); try { await api.requestJson(bandWorkspacePaths.suggestionVote(band.id, suggestion.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(votePayload(liked ? 0 : 1)) }); await onChanged(); } catch { setError('좋아요를 반영하지 못했습니다. 잠시 후 다시 시도해 주세요.'); } finally { voteFlight.current = false; setBusy(false); } };
  const proceed = async () => { if (!suggestion.can_proceed || writeFlight.current || !window.confirm('이 추천곡을 곡 목록으로 진행할까요? 이 작업은 추천글을 목록에서 제거합니다.')) return; writeFlight.current = true; setBusy(true); setError(''); try { await api.requestJson(bandWorkspacePaths.suggestionProceed(band.id, suggestion.id), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ visibility: suggestion.visibility }) }); onChanged(); } catch { setError('추천곡 진행을 완료하지 못했습니다.'); } finally { writeFlight.current = false; setBusy(false); } };
  if (editing) return <SuggestionForm item={suggestion} onDone={() => { setEditing(false); onChanged(); }} onCancel={() => setEditing(false)} />;
  return <article className="workspace-card suggestion-card">
    <header className="suggestion-card-heading">
      <div className="suggestion-card-title-block">
        <h2 className="suggestion-card-title">{suggestion.title || '제목 없음'}</h2>
        <p className="suggestion-card-artist">{suggestion.artist || '아티스트 정보 없음'}</p>
        {suggestion.status && <span className="suggestion-status">{suggestion.status === 'pending' ? '검토 대기' : suggestion.status}</span>}
      </div>
      {member && (suggestion.can_edit || suggestion.can_delete) && <div className="suggestion-manage-actions" role="group" aria-label="추천곡 관리">
        {suggestion.can_edit && <button type="button" className="secondary-btn suggestion-icon-btn" aria-label="수정" title="수정" disabled={busy} onClick={() => setEditing(true)}>
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="m16 3 5 5M4 15 15.5 3.5a2.12 2.12 0 0 1 3 0l2 2a2.12 2.12 0 0 1 0 3L9 20l-6 1 1-6Z" /></svg>
        </button>}
        <BandDeleteAction band={band} apiClient={api} workspaceKey={`suggestion:${suggestion.id}`} canDelete={suggestion.can_delete} label="추천글" path={bandWorkspacePaths.suggestion(band.id, suggestion.id)} onDeleted={onChanged} className="danger-btn suggestion-icon-btn"
          icon={<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M3 6h18M9 6V3h6v3m4 0-1 14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1L5 6m5 4v7m4-7v7" /></svg>}
        />
      </div>}
    </header>
    {suggestion.memo && <p className="suggestion-card-memo">{suggestion.memo}</p>}
    <div className="suggestion-card-meta"><span aria-live="polite">점수 {suggestion.vote_score ?? 0}</span></div>
    {videoId && <SuggestionVideo key={videoId} videoId={videoId} title={suggestion.title} />}
    {safeLink && <a className={'secondary-btn workspace-button-link suggestion-card-link' + (videoId ? ' suggestion-youtube-link' : '')} href={safeLink} target="_blank" rel="noopener noreferrer">{videoId ? 'YouTube에서 열기' : '참고 링크 열기'}</a>}
    {member ? <div className="suggestion-actions">
      <div role="group" aria-label="추천곡 투표">
        <button type="button" className="secondary-btn suggestion-vote-button" aria-pressed={liked} disabled={busy} onClick={toggleLike}>좋아요</button>
      </div>
      {suggestion.can_proceed && <button type="button" className="primary-btn suggestion-proceed" disabled={busy} onClick={proceed}>곡으로 진행</button>}
    </div> : <p className="workspace-muted">로그인 후 밴드에 가입하면 추천곡을 등록하거나 좋아요를 누를 수 있습니다.</p>}
    {error && <p className="workspace-error" role="alert">{error}</p>}
  </article>;
};
