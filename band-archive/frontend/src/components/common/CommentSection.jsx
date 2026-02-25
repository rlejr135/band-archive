import React, { useState, useEffect } from 'react';
import { fetchComments, createComment, createReply, updateComment, deleteComment } from '../../services/api';
import './CommentSection.css';

const CommentItem = ({ comment, onReply, onUpdate, onDelete, depth = 0 }) => {
  const [showReplyForm, setShowReplyForm] = useState(false);
  const [replyData, setReplyData] = useState({ author: '', password: '', content: '' });
  const [submitting, setSubmitting] = useState(false);

  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(comment.content);
  const [editPassword, setEditPassword] = useState('');
  const [editError, setEditError] = useState('');

  const [deletePassword, setDeletePassword] = useState('');
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteError, setDeleteError] = useState('');

  const handleReplySubmit = async (e) => {
    e.preventDefault();
    if (!replyData.author.trim() || !replyData.password.trim() || !replyData.content.trim()) return;
    setSubmitting(true);
    try {
      await onReply(comment.id, replyData);
      setReplyData({ author: '', password: '', content: '' });
      setShowReplyForm(false);
    } catch {
      alert('답글 작성에 실패했습니다.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleEditSubmit = async (e) => {
    e.preventDefault();
    if (!editPassword.trim() || !editContent.trim()) return;
    setEditError('');
    setSubmitting(true);
    try {
      await onUpdate(comment.id, { password: editPassword, content: editContent });
      setEditing(false);
      setEditPassword('');
    } catch {
      setEditError('비밀번호가 틀렸습니다.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteSubmit = async (e) => {
    e.preventDefault();
    if (!deletePassword.trim()) return;
    setDeleteError('');
    setSubmitting(true);
    try {
      await onDelete(comment.id, deletePassword);
      setShowDeleteConfirm(false);
      setDeletePassword('');
    } catch {
      setDeleteError('비밀번호가 틀렸습니다.');
    } finally {
      setSubmitting(false);
    }
  };

  const timeAgo = (dateStr) => {
    const diff = Date.now() - new Date(dateStr).getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) return '방금 전';
    if (minutes < 60) return `${minutes}분 전`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}시간 전`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}일 전`;
    return new Date(dateStr).toLocaleDateString();
  };

  return (
    <div className={`comment-item ${depth > 0 ? 'comment-reply' : ''}`}>
      <div className="comment-header">
        <span className="comment-author">{comment.author}</span>
        <span className="comment-time">{timeAgo(comment.created_at)}</span>
      </div>

      {editing ? (
        <form className="comment-edit-form" onSubmit={handleEditSubmit}>
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="comment-edit-textarea"
          />
          <input
            type="password"
            value={editPassword}
            onChange={(e) => setEditPassword(e.target.value)}
            placeholder="비밀번호"
            className="comment-password-input"
          />
          {editError && <span className="comment-error">{editError}</span>}
          <div className="comment-edit-actions">
            <button type="submit" disabled={submitting} className="comment-btn-save">
              {submitting ? '저장 중...' : '저장'}
            </button>
            <button type="button" onClick={() => { setEditing(false); setEditError(''); }} className="comment-btn-cancel">
              취소
            </button>
          </div>
        </form>
      ) : (
        <p className="comment-content">{comment.content}</p>
      )}

      {!editing && (
        <div className="comment-actions">
          <button className="comment-action-btn" onClick={() => setShowReplyForm(!showReplyForm)}>답글</button>
          <button className="comment-action-btn" onClick={() => { setEditing(true); setEditContent(comment.content); }}>수정</button>
          <button className="comment-action-btn comment-action-delete" onClick={() => setShowDeleteConfirm(!showDeleteConfirm)}>삭제</button>
        </div>
      )}

      {showDeleteConfirm && (
        <form className="comment-delete-form" onSubmit={handleDeleteSubmit}>
          <input
            type="password"
            value={deletePassword}
            onChange={(e) => setDeletePassword(e.target.value)}
            placeholder="비밀번호 입력 후 삭제"
            className="comment-password-input"
            autoFocus
          />
          {deleteError && <span className="comment-error">{deleteError}</span>}
          <div className="comment-edit-actions">
            <button type="submit" disabled={submitting} className="comment-btn-delete">삭제</button>
            <button type="button" onClick={() => { setShowDeleteConfirm(false); setDeleteError(''); }} className="comment-btn-cancel">취소</button>
          </div>
        </form>
      )}

      {showReplyForm && (
        <form className="comment-reply-form" onSubmit={handleReplySubmit}>
          <div className="comment-form-row">
            <input
              type="text"
              value={replyData.author}
              onChange={(e) => setReplyData({ ...replyData, author: e.target.value })}
              placeholder="이름"
              className="comment-author-input"
            />
            <input
              type="password"
              value={replyData.password}
              onChange={(e) => setReplyData({ ...replyData, password: e.target.value })}
              placeholder="비밀번호"
              className="comment-password-input"
            />
          </div>
          <textarea
            value={replyData.content}
            onChange={(e) => setReplyData({ ...replyData, content: e.target.value })}
            placeholder="답글을 입력하세요..."
            className="comment-textarea"
          />
          <div className="comment-edit-actions">
            <button type="submit" disabled={submitting} className="comment-btn-save">
              {submitting ? '등록 중...' : '답글 등록'}
            </button>
            <button type="button" onClick={() => setShowReplyForm(false)} className="comment-btn-cancel">취소</button>
          </div>
        </form>
      )}

      {comment.replies && comment.replies.length > 0 && (
        <div className="comment-replies">
          {comment.replies.map((reply) => (
            <CommentItem
              key={reply.id}
              comment={reply}
              onReply={onReply}
              onUpdate={onUpdate}
              onDelete={onDelete}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
};

const CommentSection = ({ targetType, targetId }) => {
  const [comments, setComments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [formData, setFormData] = useState({ author: '', password: '', content: '' });
  const [submitting, setSubmitting] = useState(false);

  const loadComments = async () => {
    try {
      const data = await fetchComments(targetType, targetId);
      setComments(data);
    } catch (err) {
      console.error('Failed to load comments:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setComments([]);
    setLoading(true);
    loadComments();
  }, [targetType, targetId]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!formData.author.trim() || !formData.password.trim() || !formData.content.trim()) return;
    setSubmitting(true);
    try {
      await createComment(targetType, targetId, formData);
      setFormData({ ...formData, content: '' });
      await loadComments();
    } catch {
      alert('댓글 작성에 실패했습니다.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleReply = async (commentId, data) => {
    await createReply(commentId, data);
    await loadComments();
  };

  const handleUpdate = async (commentId, data) => {
    await updateComment(commentId, data);
    await loadComments();
  };

  const handleDelete = async (commentId, password) => {
    await deleteComment(commentId, password);
    await loadComments();
  };

  return (
    <div className="comment-section">
      <h5 className="comment-section-title">댓글 {comments.length > 0 && `(${comments.length})`}</h5>

      {loading ? (
        <div className="comment-loading">로딩 중...</div>
      ) : (
        <>
          {comments.length > 0 ? (
            <div className="comment-list">
              {comments.map((comment) => (
                <CommentItem
                  key={comment.id}
                  comment={comment}
                  onReply={handleReply}
                  onUpdate={handleUpdate}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          ) : (
            <p className="comment-empty">아직 댓글이 없습니다.</p>
          )}
        </>
      )}

      <form className="comment-form" onSubmit={handleSubmit}>
        <div className="comment-form-row">
          <input
            type="text"
            value={formData.author}
            onChange={(e) => setFormData({ ...formData, author: e.target.value })}
            placeholder="이름"
            className="comment-author-input"
          />
          <input
            type="password"
            value={formData.password}
            onChange={(e) => setFormData({ ...formData, password: e.target.value })}
            placeholder="비밀번호"
            className="comment-password-input"
          />
        </div>
        <textarea
          value={formData.content}
          onChange={(e) => setFormData({ ...formData, content: e.target.value })}
          placeholder="댓글을 입력하세요..."
          className="comment-textarea"
        />
        <button type="submit" disabled={submitting} className="comment-submit-btn">
          {submitting ? '등록 중...' : '댓글 등록'}
        </button>
      </form>
    </div>
  );
};

export default CommentSection;
