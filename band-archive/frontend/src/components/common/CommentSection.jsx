import React, { useCallback, useEffect, useState } from 'react';
import { createComment, createReply, deleteComment, fetchComments, updateComment } from '../../services/api';
import { canAttachCommentTimestamp, formatCommentTimestamp, normalizeCommentTimestamp, seekCommentTimestamp } from '../../services/commentTimestamp';
import './CommentSection.css';

const TimestampControl = ({ value, onChange, playbackControllerRef, label = '재생 위치 연결' }) => {
  const [message, setMessage] = useState('');
  const displayed = value === undefined ? null : value;
  const attachCurrentTime = () => {
    const time = playbackControllerRef?.current?.getCurrentTime?.();
    if (normalizeCommentTimestamp(time) === null) { setMessage('재생 위치를 확인한 뒤 연결할 수 있습니다.'); return; }
    onChange(time);
    setMessage(`${formatCommentTimestamp(time)}에 연결했습니다.`);
  };
  return <div className="comment-timestamp-control">
    <span>{label}: {displayed === null ? '연결 안 함' : `${formatCommentTimestamp(displayed)} 연결`}</span>
    <div className="comment-timestamp-actions">
      <button type="button" onClick={attachCurrentTime} aria-label="현재 재생 위치를 댓글에 연결">현재 위치 연결</button>
      {value !== undefined && <button type="button" onClick={() => { onChange(null); setMessage('재생 위치 연결을 지웁니다.'); }} aria-label="댓글 재생 위치 연결 지우기">연결 지우기</button>}
    </div>
    {message && <span className="comment-timestamp-status" role="status">{message}</span>}
  </div>;
};

const TimestampBadge = ({ timestamp, onSeek }) => {
  const formatted = formatCommentTimestamp(timestamp);
  if (formatted === null) return null;
  return <button type="button" className="comment-timestamp-badge" onClick={() => onSeek?.(timestamp)} disabled={!onSeek} aria-label={`재생 위치 ${formatted}(으)로 이동`}>▶ {formatted}</button>;
};

const CommentItem = ({ comment, onReply, onUpdate, onDelete, onSeekTimestamp, timestampEnabled, playbackControllerRef, depth = 0 }) => {
  const [showReplyForm, setShowReplyForm] = useState(false);
  const [replyData, setReplyData] = useState({ author: '', password: '', content: '' });
  const [submitting, setSubmitting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(comment.content);
  const [editPassword, setEditPassword] = useState('');
  const [editTimestampSeconds, setEditTimestampSeconds] = useState(undefined);
  const [editError, setEditError] = useState('');
  const [deletePassword, setDeletePassword] = useState('');
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const timeAgo = (dateStr) => { const minutes = Math.floor((Date.now() - new Date(dateStr).getTime()) / 60000); if (minutes < 1) return '방금 전'; if (minutes < 60) return `${minutes}분 전`; const hours = Math.floor(minutes / 60); if (hours < 24) return `${hours}시간 전`; const days = Math.floor(hours / 24); return days < 30 ? `${days}일 전` : new Date(dateStr).toLocaleDateString(); };
  const handleReplySubmit = async (event) => { event.preventDefault(); if (!replyData.author.trim() || !replyData.password.trim() || !replyData.content.trim()) return; setSubmitting(true); try { await onReply(comment.id, replyData); setReplyData({ author: '', password: '', content: '' }); setShowReplyForm(false); } catch { alert('답글 작성에 실패했습니다.'); } finally { setSubmitting(false); } };
  const handleEditSubmit = async (event) => { event.preventDefault(); if (!editPassword.trim() || !editContent.trim()) return; setEditError(''); setSubmitting(true); try { await onUpdate(comment.id, { password: editPassword, content: editContent, timestampSeconds: editTimestampSeconds }); setEditing(false); setEditPassword(''); setEditTimestampSeconds(undefined); } catch { setEditError('비밀번호가 틀렸습니다.'); } finally { setSubmitting(false); } };
  const handleDeleteSubmit = async (event) => { event.preventDefault(); if (!deletePassword.trim()) return; setDeleteError(''); setSubmitting(true); try { await onDelete(comment.id, deletePassword); setShowDeleteConfirm(false); setDeletePassword(''); } catch { setDeleteError('비밀번호가 틀렸습니다.'); } finally { setSubmitting(false); } };
  const isTopLevel = canAttachCommentTimestamp({ timestampEnabled, depth });
  const editDisplayTimestamp = editTimestampSeconds === undefined ? comment.timestamp_seconds : editTimestampSeconds;

  return <div className={`comment-item ${depth > 0 ? 'comment-reply' : ''}`}>
    <div className="comment-header"><span className="comment-author">{comment.author}</span><span className="comment-time">{timeAgo(comment.created_at)}</span><TimestampBadge timestamp={comment.timestamp_seconds} onSeek={onSeekTimestamp} /></div>
    {editing ? <form className="comment-edit-form" onSubmit={handleEditSubmit}>
      <textarea value={editContent} onChange={(event) => setEditContent(event.target.value)} className="comment-edit-textarea" />
      {isTopLevel && timestampEnabled && <TimestampControl value={editTimestampSeconds === undefined ? (editDisplayTimestamp === null || editDisplayTimestamp === undefined ? undefined : editDisplayTimestamp) : editTimestampSeconds} onChange={setEditTimestampSeconds} playbackControllerRef={playbackControllerRef} label="수정할 재생 위치" />}
      <input type="password" value={editPassword} onChange={(event) => setEditPassword(event.target.value)} placeholder="비밀번호" className="comment-password-input" />
      {editError && <span className="comment-error">{editError}</span>}<div className="comment-edit-actions"><button type="submit" disabled={submitting} className="comment-btn-save">{submitting ? '저장 중...' : '저장'}</button><button type="button" onClick={() => { setEditing(false); setEditError(''); setEditTimestampSeconds(undefined); }} className="comment-btn-cancel">취소</button></div>
    </form> : <p className="comment-content">{comment.content}</p>}
    {!editing && <div className="comment-actions"><button className="comment-action-btn" onClick={() => setShowReplyForm(!showReplyForm)}>답글</button><button className="comment-action-btn" onClick={() => { setEditing(true); setEditContent(comment.content); setEditTimestampSeconds(undefined); }}>수정</button><button className="comment-action-btn comment-action-delete" onClick={() => setShowDeleteConfirm(!showDeleteConfirm)}>삭제</button></div>}
    {showDeleteConfirm && <form className="comment-delete-form" onSubmit={handleDeleteSubmit}><input type="password" value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} placeholder="비밀번호 입력 후 삭제" className="comment-password-input" autoFocus />{deleteError && <span className="comment-error">{deleteError}</span>}<div className="comment-edit-actions"><button type="submit" disabled={submitting} className="comment-btn-delete">삭제</button><button type="button" onClick={() => { setShowDeleteConfirm(false); setDeleteError(''); }} className="comment-btn-cancel">취소</button></div></form>}
    {showReplyForm && <form className="comment-reply-form" onSubmit={handleReplySubmit}><div className="comment-form-row"><input type="text" value={replyData.author} onChange={(event) => setReplyData({ ...replyData, author: event.target.value })} placeholder="이름" className="comment-author-input" /><input type="password" value={replyData.password} onChange={(event) => setReplyData({ ...replyData, password: event.target.value })} placeholder="비밀번호" className="comment-password-input" /></div><textarea value={replyData.content} onChange={(event) => setReplyData({ ...replyData, content: event.target.value })} placeholder="답글을 입력하세요..." className="comment-textarea" /><div className="comment-edit-actions"><button type="submit" disabled={submitting} className="comment-btn-save">{submitting ? '등록 중...' : '답글 등록'}</button><button type="button" onClick={() => setShowReplyForm(false)} className="comment-btn-cancel">취소</button></div></form>}
    {comment.replies?.length > 0 && <div className="comment-replies">{comment.replies.map((reply) => <CommentItem key={reply.id} comment={reply} onReply={onReply} onUpdate={onUpdate} onDelete={onDelete} onSeekTimestamp={onSeekTimestamp} timestampEnabled={false} playbackControllerRef={playbackControllerRef} depth={depth + 1} />)}</div>}
  </div>;
};

const CommentSection = ({ targetType, targetId, timestampEnabled = false, playbackControllerRef = null }) => {
  const [comments, setComments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [formData, setFormData] = useState({ author: '', password: '', content: '' });
  const [timestampSeconds, setTimestampSeconds] = useState(undefined);
  const [submitting, setSubmitting] = useState(false);
  const [seekStatus, setSeekStatus] = useState('');
  const loadComments = useCallback(async () => { try { setComments(await fetchComments(targetType, targetId)); } catch (error) { console.error('Failed to load comments:', error); } finally { setLoading(false); } }, [targetId, targetType]);
  useEffect(() => { setComments([]); setLoading(true); loadComments(); }, [loadComments]);
  const handleSubmit = async (event) => { event.preventDefault(); if (!formData.author.trim() || !formData.password.trim() || !formData.content.trim()) return; setSubmitting(true); try { await createComment(targetType, targetId, { ...formData, timestampSeconds }); setFormData({ ...formData, content: '' }); setTimestampSeconds(undefined); await loadComments(); } catch { alert('댓글 작성에 실패했습니다.'); } finally { setSubmitting(false); } };
  const handleSeekTimestamp = (timestamp) => {
    const didSeek = seekCommentTimestamp(playbackControllerRef?.current, timestamp);
    setSeekStatus(didSeek ? `${formatCommentTimestamp(timestamp)} 위치로 이동했습니다.` : '이 댓글의 재생 위치를 사용할 수 없습니다.');
  };
  return <div className="comment-section"><h5 className="comment-section-title">댓글 {comments.length > 0 && `(${comments.length})`}</h5>{loading ? <div className="comment-loading">로딩 중...</div> : comments.length > 0 ? <div className="comment-list">{comments.map((comment) => <CommentItem key={comment.id} comment={comment} onReply={async (commentId, data) => { await createReply(commentId, data); await loadComments(); }} onUpdate={async (commentId, data) => { await updateComment(commentId, data); await loadComments(); }} onDelete={async (commentId, password) => { await deleteComment(commentId, password); await loadComments(); }} onSeekTimestamp={timestampEnabled ? handleSeekTimestamp : null} timestampEnabled={timestampEnabled} playbackControllerRef={playbackControllerRef} />)}</div> : <p className="comment-empty">아직 댓글이 없습니다.</p>}
    {seekStatus && <p className="comment-timestamp-status" role="status">{seekStatus}</p>}
    <form className="comment-form" onSubmit={handleSubmit}><div className="comment-form-row"><input type="text" value={formData.author} onChange={(event) => setFormData({ ...formData, author: event.target.value })} placeholder="이름" className="comment-author-input" /><input type="password" value={formData.password} onChange={(event) => setFormData({ ...formData, password: event.target.value })} placeholder="비밀번호" className="comment-password-input" /></div><textarea value={formData.content} onChange={(event) => setFormData({ ...formData, content: event.target.value })} placeholder="댓글을 입력하세요..." className="comment-textarea" />{timestampEnabled && <TimestampControl value={timestampSeconds} onChange={setTimestampSeconds} playbackControllerRef={playbackControllerRef} />}<button type="submit" disabled={submitting} className="comment-submit-btn">{submitting ? '등록 중...' : '댓글 등록'}</button></form>
  </div>;
};

export default CommentSection;
