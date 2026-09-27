import { useCallback, useEffect, useRef, useState } from 'react';
import { bandWorkspacePaths, commentPayload, votePayload } from '../../services/bandWorkspaceApi.js';
import { buildCommentThreads, mergeCommentPage, voteTarget } from '../../services/mediaEngagement.js';

const PAGE_SIZE = 20;
const readOptions = (signal) => ({ authenticated: false, optionalAuth: true, signal });
const writeOptions = (method, body, signal) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
const genericError = '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.';
const activeMembership = (membership) => Boolean(membership && !['inactive', 'removed', 'suspended'].includes(String(membership.status || '').toLowerCase()));

const CommentNode = ({ comment, member, editing, editValue, onEditValue, onEdit, onDelete, onReply, onSaveEdit, onCancelEdit, busy }) => (
  <li className="media-comment" data-comment-id={comment.id}>
    <div className="media-comment-meta"><strong>{comment.author || '멤버'}</strong><span>{comment.created_at ? new Date(comment.created_at).toLocaleString() : ''}</span></div>
    {editing === comment.id ? <div className="media-comment-edit"><textarea aria-label="댓글 수정" value={editValue} onChange={(event) => onEditValue(event.target.value)} /><button type="button" className="secondary-btn" disabled={busy} onClick={() => onSaveEdit(comment.id)}>저장</button><button type="button" className="secondary-btn" disabled={busy} onClick={onCancelEdit}>취소</button></div> : <p>{comment.content}</p>}
    {member && editing !== comment.id && <div className="media-comment-actions"><button type="button" onClick={() => onReply(comment.id)}>답글</button>{comment.can_edit && <button type="button" onClick={() => onEdit(comment)}>수정</button>}{comment.can_delete && <button type="button" onClick={() => onDelete(comment.id)} disabled={busy}>삭제</button>}</div>}
    {comment.replies?.length > 0 && <ul className="media-comment-replies">{comment.replies.map((reply) => <CommentNode key={reply.id} comment={reply} member={member} editing={editing} editValue={editValue} onEditValue={onEditValue} onEdit={onEdit} onDelete={onDelete} onReply={onReply} onSaveEdit={onSaveEdit} onCancelEdit={onCancelEdit} busy={busy} />)}</ul>}
  </li>
);

export const MediaEngagement = ({ band, api, media, onMediaUpdate, onAuthoritativeRefresh }) => {
  const member = activeMembership(band.membership);
  const [viewerVote, setViewerVote] = useState(Number.isInteger(media.viewer_vote) ? media.viewer_vote : 0);
  const [voteBusy, setVoteBusy] = useState(false);
  const [voteError, setVoteError] = useState('');
  const [comments, setComments] = useState({ loading: true, loadingMore: false, error: '', entries: [], pagination: null });
  const [draft, setDraft] = useState(''); const [replyTo, setReplyTo] = useState(null);
  const [editing, setEditing] = useState(null); const [editValue, setEditValue] = useState('');
  const [writeBusy, setWriteBusy] = useState(false); const [commentError, setCommentError] = useState('');
  const live = useRef(true); const commentControllers = useRef(new Set()); const commentVersion = useRef(0); const writeFlight = useRef(false);

  const loadComments = useCallback(async (offset = 0, append = false) => {
    const controller = new AbortController(); commentControllers.current.add(controller); const version = ++commentVersion.current;
    setComments((previous) => ({ ...previous, [append ? 'loadingMore' : 'loading']: true, error: '' }));
    try {
      const response = await api.requestJson(bandWorkspacePaths.mediaComments(band.id, media.id, { limit: PAGE_SIZE, offset }), readOptions(controller.signal));
      if (!live.current || controller.signal.aborted || version !== commentVersion.current) return;
      const page = Array.isArray(response?.comments) ? response.comments : [];
      setComments((previous) => ({ loading: false, loadingMore: false, error: '', entries: append ? mergeCommentPage(previous.entries, page) : mergeCommentPage([], page), pagination: response?.pagination ?? null }));
    } catch {
      if (live.current && !controller.signal.aborted && version === commentVersion.current) setComments((previous) => ({ ...previous, loading: false, loadingMore: false, error: genericError }));
    } finally { commentControllers.current.delete(controller); }
  }, [api, band.id, media.id]);
  useEffect(() => { const controllers = commentControllers.current; live.current = true; loadComments(); return () => { live.current = false; commentVersion.current += 1; controllers.forEach((controller) => controller.abort()); controllers.clear(); }; }, [loadComments]);

  const vote = async (clickedValue) => {
    if (!member || voteBusy) return;
    setVoteBusy(true); setVoteError('');
    try {
      const response = await api.requestJson(bandWorkspacePaths.mediaVote(band.id, media.id), writeOptions('PUT', votePayload(voteTarget(viewerVote, clickedValue))));
      if (!live.current) return;
      if (Number.isInteger(response?.viewer_vote)) setViewerVote(response.viewer_vote);
      if (response?.media) onMediaUpdate(response.media);
      // Media ranking is owned by the server. Refresh the page after every
      // mutation so score changes cannot leave a locally sorted list behind.
      onAuthoritativeRefresh();
    } catch { if (live.current) setVoteError(genericError); } finally { if (live.current) setVoteBusy(false); }
  };
  const postComment = async () => {
    if (!member || writeFlight.current || !draft.trim()) return;
    writeFlight.current = true; setWriteBusy(true); setCommentError('');
    try {
      const response = await api.requestJson(bandWorkspacePaths.mediaCommentsWrite(band.id, media.id), writeOptions('POST', commentPayload({ content: draft.trim(), parentId: replyTo })));
      if (!live.current) return;
      if (response?.comment) setComments((previous) => ({ ...previous, entries: mergeCommentPage(previous.entries, [response.comment]) }));
      setDraft(''); setReplyTo(null);
    } catch { if (live.current) setCommentError(genericError); } finally { writeFlight.current = false; if (live.current) setWriteBusy(false); }
  };
  const saveEdit = async (commentId) => {
    if (!member || writeFlight.current || !editValue.trim()) return;
    writeFlight.current = true; setWriteBusy(true); setCommentError('');
    try {
      const response = await api.requestJson(bandWorkspacePaths.comment(band.id, commentId), writeOptions('PATCH', { content: editValue.trim() }));
      if (!live.current) return;
      if (response?.comment) setComments((previous) => ({ ...previous, entries: mergeCommentPage([], previous.entries.map((item) => item.id === commentId ? response.comment : item)) }));
      setEditing(null); setEditValue('');
    } catch { if (live.current) setCommentError(genericError); } finally { writeFlight.current = false; if (live.current) setWriteBusy(false); }
  };
  const deleteComment = async (commentId) => {
    if (!member || writeFlight.current || !window.confirm('이 댓글을 삭제할까요?')) return;
    writeFlight.current = true; setWriteBusy(true); setCommentError('');
    try {
      await api.requestJson(bandWorkspacePaths.comment(band.id, commentId), { method: 'DELETE' });
      if (!live.current) return;
      // A delete can cascade descendants, so reload instead of guessing which
      // rows disappeared from the flat feed.
      setDraft(''); setReplyTo(null); setEditing(null); setEditValue(''); loadComments();
    } catch { if (live.current) setCommentError(genericError); } finally { writeFlight.current = false; if (live.current) setWriteBusy(false); }
  };
  const threads = buildCommentThreads(comments.entries);
  return <section className="media-engagement" aria-label="미디어 반응과 댓글">
    <div className="media-vote" role="group" aria-label="미디어 평가"><span aria-live="polite">점수 {media.vote_score ?? 0}</span>{member ? <><button type="button" aria-pressed={viewerVote === 1} disabled={voteBusy} onClick={() => vote(1)}>추천</button><button type="button" aria-pressed={viewerVote === -1} disabled={voteBusy} onClick={() => vote(-1)}>아쉬워요</button></> : <span className="workspace-muted">{band.membership ? '활성 멤버만 평가할 수 있습니다.' : '로그인 후 밴드에 가입하면 평가할 수 있습니다.'}</span>}</div>
    {voteError && <p className="workspace-error" role="alert">{voteError}</p>}
    <div className="media-comments"><h4>댓글</h4>{comments.loading && comments.entries.length === 0 ? <p className="workspace-muted">댓글을 불러오는 중입니다…</p> : <>{threads.length === 0 ? <p className="workspace-muted">아직 댓글이 없습니다.</p> : <ul className="media-comment-list">{threads.map((comment) => <CommentNode key={comment.id} comment={comment} member={member} editing={editing} editValue={editValue} onEditValue={setEditValue} onEdit={(item) => { setEditing(item.id); setEditValue(item.content || ''); setReplyTo(null); }} onDelete={deleteComment} onReply={(id) => { setReplyTo(id); setEditing(null); setEditValue(''); }} onSaveEdit={saveEdit} onCancelEdit={() => { setEditing(null); setEditValue(''); }} busy={writeBusy} />)}</ul>}{comments.pagination?.has_more && <button type="button" className="secondary-btn" disabled={comments.loadingMore} onClick={() => loadComments(comments.pagination.next_offset, true)}>{comments.loadingMore ? '댓글을 불러오는 중…' : '댓글 더 불러오기'}</button>}</>}{comments.error && <p className="workspace-error" role="alert">{comments.error}</p>}
      {member ? <form className="media-comment-form" onSubmit={(event) => { event.preventDefault(); postComment(); }}><label>{replyTo == null ? '댓글 작성' : `댓글 ${replyTo}에 답글 작성`}<textarea aria-label="댓글 내용" value={draft} onChange={(event) => setDraft(event.target.value)} /></label><div><button type="submit" className="primary-btn" disabled={writeBusy || !draft.trim()}>{writeBusy ? '저장 중…' : '댓글 등록'}</button>{replyTo != null && <button type="button" className="secondary-btn" disabled={writeBusy} onClick={() => setReplyTo(null)}>답글 취소</button>}</div></form> : <p className="workspace-muted">{band.membership ? '활성 멤버만 댓글을 작성할 수 있습니다.' : '로그인 후 밴드에 가입하면 댓글을 작성할 수 있습니다.'}</p>}{commentError && <p className="workspace-error" role="alert">{commentError}</p>}</div>
  </section>;
};
