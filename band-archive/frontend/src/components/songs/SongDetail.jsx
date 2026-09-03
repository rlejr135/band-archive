import React, { useState, useEffect } from 'react';
import { useSongs } from '../../context/SongContext';
import { linkMediaToRehearsal } from '../../services/api';
import { fetchRehearsals } from '../../services/rehearsalApi';
import './SongDetail.css';
import './SongMedia.css';
import FileUpload from '../common/FileUpload';
import MediaPlayer from '../common/MediaPlayer';
import CommentSection from '../common/CommentSection';
import { sortMediaByScore } from '../../services/mediaVoting.js';
import { getMediaIcon, getMediaType } from '../../services/mediaPresentation.js';
import { getYoutubeId } from '../../services/youtube.js';

const SongDetail = ({ song, onEdit, onBack }) => {
  const [expandedMediaIds, setExpandedMediaIds] = useState(new Set());
  const { removeMediaFromSong, renameMediaInSong, editSong, refreshSong, voteForMedia, voteStates } = useSongs();

  const [renamingMediaId, setRenamingMediaId] = useState(null);
  const [newFilename, setNewFilename] = useState('');
  const [editingChords, setEditingChords] = useState(false);
  const [chordsText, setChordsText] = useState(song?.chords || '');
  const [chordsSaving, setChordsSaving] = useState(false);
  const [editingMemo, setEditingMemo] = useState(false);
  const [memoText, setMemoText] = useState(song?.memo || '');
  const [memoSaving, setMemoSaving] = useState(false);
  const [rehearsalPickerMediaId, setRehearsalPickerMediaId] = useState(null);
  const [allRehearsals, setAllRehearsals] = useState([]);

  useEffect(() => {
    fetchRehearsals().then(setAllRehearsals).catch(console.error);
  }, []);

  useEffect(() => {
    setExpandedMediaIds(new Set());
    setRenamingMediaId(null);
    setNewFilename('');
    setEditingChords(false);
    setChordsText(song?.chords || '');
    setEditingMemo(false);
    setMemoText(song?.memo || '');
    setRehearsalPickerMediaId(null);
  }, [song?.id]);

  if (!song) {
    return <div className="song-detail-placeholder">곡을 선택해주세요.</div>;
  }

  // Clean filename for display (remove id_timestamp_ prefix)
  const getDisplayName = (filename) => {
    const parts = filename.split('_');
    // Check if filename starts with id_timestamp_ format
    if (parts.length >= 3 && /^\d+$/.test(parts[0]) && /^\d{8}$/.test(parts[1])) {
      return parts.slice(2).join('_');
    }
    // Try matching simpler pattern or just return filename if not matching
    return filename;
  };

  const handleStartRename = (media) => {
    setRenamingMediaId(media.id);
    setNewFilename(getDisplayName(media.filename));
  };

  const handleCancelRename = () => {
    setRenamingMediaId(null);
    setNewFilename('');
  };

  const handleSaveRename = async (mediaId) => {
    if (!newFilename.trim()) return;
    try {
      await renameMediaInSong(song.id, mediaId, newFilename);
      setRenamingMediaId(null);
      setNewFilename('');
    } catch {
      alert('파일 이름 변경에 실패했습니다.');
    }
  };

  const handleChordsEdit = () => {
    setChordsText(song.chords || '');
    setEditingChords(true);
  };

  const handleChordsCancel = () => {
    setEditingChords(false);
    setChordsText(song.chords || '');
  };

  const handleChordsSave = async () => {
    setChordsSaving(true);
    try {
      await editSong(song.id, { ...song, chords: chordsText });
      setEditingChords(false);
    } catch {
      alert('코드 저장에 실패했습니다.');
    } finally {
      setChordsSaving(false);
    }
  };

  const handleMemoEdit = () => {
    setMemoText(song.memo || '');
    setEditingMemo(true);
  };

  const handleMemoCancel = () => {
    setEditingMemo(false);
    setMemoText(song.memo || '');
  };

  const handleMemoSave = async () => {
    setMemoSaving(true);
    try {
      await editSong(song.id, { ...song, memo: memoText });
      setEditingMemo(false);
    } catch {
      alert('메모 저장에 실패했습니다.');
    } finally {
      setMemoSaving(false);
    }
  };

  const handleLinkRehearsal = async (mediaId, rehearsalId) => {
    try {
      await linkMediaToRehearsal(mediaId, rehearsalId || null);
      setRehearsalPickerMediaId(null);
      await refreshSong(song.id);
    } catch {
      alert('합주 연결에 실패했습니다.');
    }
  };

  const handleDeleteMedia = async (mediaId) => {
    if (!window.confirm('정말 이 파일을 삭제하시겠습니까?')) return;
    try {
      await removeMediaFromSong(song.id, mediaId);
    } catch {
      alert('파일 삭제에 실패했습니다.');
    }
  };

  const toggleMediaExpand = (mediaId) => {
    setExpandedMediaIds(prev => {
      const next = new Set(prev);
      if (next.has(mediaId)) {
        next.delete(mediaId);
      } else {
        next.add(mediaId);
      }
      return next;
    });
  };

  const handleMediaVote = (event, mediaId, vote) => {
    event.stopPropagation();
    voteForMedia(mediaId, vote);
  };

  const statusLabel = { Practice: '연습중', Completed: '완료', OnHold: '보류' };

  return (
    <div className="song-detail">
      {onBack && (
        <button className="mobile-back-btn" onClick={onBack}>
          ← 목록으로
        </button>
      )}

      <h2>{song.title}</h2>
      <h3>{song.artist}</h3>

      <div className="song-info">
        <p><strong>상태:</strong> <span className={`status-badge ${song.status?.toLowerCase()}`}>{statusLabel[song.status] || song.status}</span></p>
        <p><strong>장르:</strong> {song.genre || '-'}</p>
        <p><strong>난이도:</strong> {'⭐'.repeat(song.difficulty)}</p>
        {song.link && (
          <p><strong>링크:</strong> <a href={song.link} target="_blank" rel="noreferrer">{song.link}</a></p>
        )}
      </div>

      {song.link && getYoutubeId(song.link) && (
        <div className="youtube-embed">
          <h4>원곡 영상</h4>
          <iframe
            src={`https://www.youtube.com/embed/${getYoutubeId(song.link)}`}
            title="YouTube video"
            allowFullScreen
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          />
        </div>
      )}

      <div className="song-content">

        <div className="song-chords">
          <div className="chords-header">
            <h4>코드</h4>
            {!editingChords && (
              <button className="chords-edit-btn" onClick={handleChordsEdit}>
                {song.chords ? '✏️ 수정' : '✏️ 작성'}
              </button>
            )}
          </div>
          {editingChords ? (
            <div className="chords-editor">
              <textarea
                className="chords-textarea"
                value={chordsText}
                onChange={(e) => setChordsText(e.target.value)}
                placeholder="코드를 입력하세요..."
                autoFocus
              />
              <div className="chords-actions">
                <button className="chords-save-btn" onClick={handleChordsSave} disabled={chordsSaving}>
                  {chordsSaving ? '저장 중...' : '💾 저장'}
                </button>
                <button className="chords-cancel-btn" onClick={handleChordsCancel} disabled={chordsSaving}>
                  취소
                </button>
              </div>
            </div>
          ) : (
            <pre className={!song.chords ? 'content-empty' : ''}>
              {song.chords || '등록된 코드가 없습니다. ✏️ 작성 버튼을 눌러 코드를 추가하세요.'}
            </pre>
          )}
        </div>
        <div className="song-memo">
          <div className="memo-header">
            <h4>메모</h4>
            {!editingMemo && (
              <button className="memo-edit-btn" onClick={handleMemoEdit}>
                {song.memo ? '✏️ 수정' : '✏️ 작성'}
              </button>
            )}
          </div>
          {editingMemo ? (
            <div className="memo-editor">
              <textarea
                className="memo-textarea"
                value={memoText}
                onChange={(e) => setMemoText(e.target.value)}
                placeholder="메모를 입력하세요..."
                autoFocus
              />
              <div className="memo-actions">
                <button className="memo-save-btn" onClick={handleMemoSave} disabled={memoSaving}>
                  {memoSaving ? '저장 중...' : '💾 저장'}
                </button>
                <button className="memo-cancel-btn" onClick={handleMemoCancel} disabled={memoSaving}>
                  취소
                </button>
              </div>
            </div>
          ) : (
            <pre className={!song.memo ? 'memo-empty' : ''}>
              {song.memo || '등록된 메모가 없습니다. ✏️ 작성 버튼을 눌러 메모를 추가하세요.'}
            </pre>
          )}
        </div>
      </div>
      <div className="song-media">
        {/* Upload with optional rehearsal link */}
        <div className="upload-with-rehearsal">
          <FileUpload songId={song.id} onMediaComplete={() => refreshSong(song.id)} />
        </div>

        {/* Media List */}
        {song.media?.length > 0 ? (
          <div className="media-list">
            {sortMediaByScore(song.media).map((media) => {
              const type = getMediaType(media);
              const isRenaming = renamingMediaId === media.id;
              const isExpanded = expandedMediaIds.has(media.id);
              const voteState = voteStates[media.id] || {};
              const viewerVote = media.viewer_vote === 1 || media.viewer_vote === -1 ? media.viewer_vote : 0;
              const score = Number.isFinite(Number(media.vote_score)) ? Number(media.vote_score) : 0;

              return (
                <div key={media.id} className={`media-item ${isExpanded ? 'expanded' : ''}`}>
                  <div className="media-item-header" onClick={() => !isRenaming && toggleMediaExpand(media.id)}>
                    <span className="media-icon">{getMediaIcon(media)}</span>

                    <div className="media-info">
                      {isRenaming ? (
                        <div className="rename-input-group" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="text"
                            value={newFilename}
                            onChange={(e) => setNewFilename(e.target.value)}
                            className="rename-input"
                            autoFocus
                          />
                          <button onClick={() => handleSaveRename(media.id)} className="save-btn">💾</button>
                          <button onClick={handleCancelRename} className="cancel-btn">❌</button>
                        </div>
                      ) : (
                        <span className="media-name">
                          {getDisplayName(media.filename)}
                          <button className="rename-btn" onClick={(e) => { e.stopPropagation(); handleStartRename(media); }} title="이름 변경">✏️</button>
                        </span>
                      )}
                      <div className="media-summary" aria-label={`${getDisplayName(media.filename)} 요약`}>
                        <span className={`media-score-summary ${score > 0 ? 'positive' : score < 0 ? 'negative' : ''}`} aria-label={`현재 점수 ${score}`}>
                          <strong>{score > 0 ? '+' : ''}{score}</strong>
                          <span>점수</span>
                        </span>
                        {media.rehearsal_title ? (
                          <span className="media-rehearsal-summary">📅 {media.rehearsal_date} {media.rehearsal_title}</span>
                        ) : (
                          <span className="media-rehearsal-summary unlinked">합주 미연결</span>
                        )}
                      </div>
                    </div>

                    <span className="expand-indicator">{isExpanded ? '▲' : '▼'}</span>
                  </div>

                  {isExpanded && (
                    <div className="media-item-body">
                      <div className="media-expanded-meta">
                        <div className="media-vote-controls" role="group" aria-label={`${getDisplayName(media.filename)} 투표`}>
                          <span className="media-vote-label" aria-hidden="true">밴드 픽</span>
                          <button
                            type="button"
                            className={`media-vote-btn vote-up ${viewerVote === 1 ? 'active' : ''}`}
                            onClick={(event) => handleMediaVote(event, media.id, 1)}
                            disabled={voteState.loading}
                            aria-pressed={viewerVote === 1}
                            aria-label={`${getDisplayName(media.filename)} 추천${viewerVote === 1 ? ' 취소' : ''}`}
                            title={viewerVote === 1 ? '추천 취소' : '이 연습 파일 추천'}
                          >
                            <span className="media-vote-icon" aria-hidden="true">👍</span>
                            <span className="media-vote-action">추천</span>
                            <span className="media-vote-count">{media.upvote_count ?? 0}</span>
                          </button>
                          <span className={`media-vote-score ${score > 0 ? 'positive' : score < 0 ? 'negative' : ''}`} aria-label={`현재 점수 ${score}`}>
                            <strong>{score > 0 ? '+' : ''}{score}</strong>
                            <span>점수</span>
                          </span>
                          <button
                            type="button"
                            className={`media-vote-btn vote-down ${viewerVote === -1 ? 'active' : ''}`}
                            onClick={(event) => handleMediaVote(event, media.id, -1)}
                            disabled={voteState.loading}
                            aria-pressed={viewerVote === -1}
                            aria-label={`${getDisplayName(media.filename)} 비추천${viewerVote === -1 ? ' 취소' : ''}`}
                            title={viewerVote === -1 ? '비추천 취소' : '이 연습 파일 비추천'}
                          >
                            <span className="media-vote-icon" aria-hidden="true">👎</span>
                            <span className="media-vote-action">아쉬워요</span>
                            <span className="media-vote-count">{media.downvote_count ?? 0}</span>
                          </button>
                          {voteState.error && <span className="media-vote-error" role="alert">{voteState.error}</span>}
                        </div>
                        <div className="media-expanded-details">
                          <span className="media-size">
                            용량 {(media.file_size / 1024 / 1024).toFixed(2)} MB
                            {media.comment_count > 0 && <span className="comment-count-badge">💬 {media.comment_count}</span>}
                          </span>
                          {rehearsalPickerMediaId === media.id ? (
                            <div className="rehearsal-picker">
                              <select
                                value={media.rehearsal_id || ''}
                                onChange={(e) => handleLinkRehearsal(media.id, e.target.value || null)}
                              >
                                <option value="">연결 없음</option>
                                {allRehearsals.map(r => (
                                  <option key={r.id} value={r.id}>{r.date} {r.title}</option>
                                ))}
                              </select>
                              <button className="picker-cancel-btn" onClick={() => setRehearsalPickerMediaId(null)}>취소</button>
                            </div>
                          ) : (
                            media.rehearsal_title ? (
                              <button className="rehearsal-badge" onClick={() => setRehearsalPickerMediaId(media.id)}>
                                📅 {media.rehearsal_date} {media.rehearsal_title}
                              </button>
                            ) : allRehearsals.length > 0 ? (
                              <button className="link-rehearsal-btn" onClick={() => setRehearsalPickerMediaId(media.id)}>
                                📅 합주 연결
                              </button>
                            ) : null
                          )}
                        </div>
                      </div>
                      <div className="media-body-actions">
                        <button className="media-delete-btn" onClick={() => handleDeleteMedia(media.id)}>🗑️ 삭제</button>
                      </div>
                      <MediaPlayer file={{
                        id: media.id,
                        name: getDisplayName(media.filename),
                        url: media.url,
                        type: type,
                        audio_url: media.audio_url,
                        audio_filename: media.audio_filename,
                        transcoding_status: media.transcoding_status,
                        transcoding_error: media.transcoding_error || media.error,
                      }} onMediaUpdate={() => refreshSong(song.id)} />
                      <CommentSection targetType="media" targetId={media.id} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="empty-media">
            <p>등록된 미디어 파일이 없습니다.</p>
            <p className="upload-instruction">위의 영역에 파일을 드래그하여 업로드하세요</p>
          </div>
        )}
      </div>

      <div className="detail-actions">
        <button onClick={() => onEdit(song)} className="edit-btn">정보 수정</button>
      </div>
    </div>
  );
};

export default SongDetail;
