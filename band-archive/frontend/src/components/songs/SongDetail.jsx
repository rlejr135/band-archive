import React, { useState, useEffect } from 'react';
import { useSongs } from '../../context/SongContext';
import { linkMediaToRehearsal } from '../../services/api';
import { fetchRehearsals } from '../../services/rehearsalApi';
import './SongDetail.css';
import './SongMedia.css';
import FileUpload from '../common/FileUpload';
import MediaPlayer from '../common/MediaPlayer';
import CommentSection from '../common/CommentSection';

const SongDetail = ({ song, onEdit, onUploadMedia, onBack }) => {
  const [selectedMedia, setSelectedMedia] = useState(null);
  const { removeMediaFromSong, renameMediaInSong, editSong, refreshSong } = useSongs();

  const [renamingMediaId, setRenamingMediaId] = useState(null);
  const [newFilename, setNewFilename] = useState('');
  const [editingChords, setEditingChords] = useState(false);
  const [chordsText, setChordsText] = useState(song?.chords || '');
  const [chordsSaving, setChordsSaving] = useState(false);
  const [editingMemo, setEditingMemo] = useState(false);
  const [memoText, setMemoText] = useState(song?.memo || '');
  const [memoSaving, setMemoSaving] = useState(false);
  const [uploadRehearsalId, setUploadRehearsalId] = useState('');
  const [rehearsalPickerMediaId, setRehearsalPickerMediaId] = useState(null);
  const [allRehearsals, setAllRehearsals] = useState([]);

  useEffect(() => {
    fetchRehearsals().then(setAllRehearsals).catch(console.error);
  }, []);

  useEffect(() => {
    setSelectedMedia(null);
    setRenamingMediaId(null);
    setNewFilename('');
    setEditingChords(false);
    setChordsText(song?.chords || '');
    setEditingMemo(false);
    setMemoText(song?.memo || '');
    setUploadRehearsalId('');
    setRehearsalPickerMediaId(null);
  }, [song?.id]);

  const getYoutubeId = (url) => {
    const match = url.match(/(?:v=|youtu\.be\/|shorts\/)([\w-]+)/);
    return match ? match[1] : null;
  };

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
    } catch (err) {
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
    } catch (err) {
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
    } catch (err) {
      alert('메모 저장에 실패했습니다.');
    } finally {
      setMemoSaving(false);
    }
  };

  const handleUpload = async (file, onProgress) => {
    await onUploadMedia(song.id, file, onProgress, uploadRehearsalId || null);
    setUploadRehearsalId('');
  };

  const handleLinkRehearsal = async (mediaId, rehearsalId) => {
    try {
      await linkMediaToRehearsal(mediaId, rehearsalId || null);
      setRehearsalPickerMediaId(null);
      await refreshSong(song.id);
    } catch (err) {
      alert('합주 연결에 실패했습니다.');
    }
  };

  const handleDeleteMedia = async (mediaId) => {
    if (!window.confirm('정말 이 파일을 삭제하시겠습니까?')) return;
    try {
      await removeMediaFromSong(song.id, mediaId);
      if (selectedMedia && selectedMedia.id === mediaId) {
        setSelectedMedia(null);
      }
    } catch (err) {
      alert('파일 삭제에 실패했습니다.');
    }
  };

  // Robust file type detection
  const getMediaType = (media) => {
    // Priority 1: Backend file_type if valid
    if (media.file_type && media.file_type !== 'document') return media.file_type;

    // Priority 2: Extension based fallback
    const ext = media.filename?.split('.').pop().toLowerCase();

    if (['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'].includes(ext)) return 'audio';
    if (['mp4', 'webm', 'mov', 'avi', 'mkv'].includes(ext)) return 'video';
    if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext)) return 'image';

    return 'document';
  };

  const iconForType = (media) => {
    const type = getMediaType(media);
    switch (type) {
      case 'video': return '🎬';
      case 'audio': return '🎵';
      case 'image': return '🖼️';
      case 'document': return '📄';
      default: return '📁';
    }
  };

  const handlePlay = (media) => {
    setSelectedMedia({
      id: media.id,
      name: getDisplayName(media.filename),
      url: media.url,
      type: getMediaType(media),
    });
  };

  const handlePreview = (media) => {
    setSelectedMedia({
      id: media.id,
      name: getDisplayName(media.filename),
      url: media.url,
      type: getMediaType(media),
    });
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
        <h4>미디어 파일</h4>

        {/* Upload with optional rehearsal link */}
        <div className="upload-with-rehearsal">
          {allRehearsals.length > 0 && (
            <select
              className="rehearsal-select"
              value={uploadRehearsalId}
              onChange={(e) => setUploadRehearsalId(e.target.value)}
            >
              <option value="">합주 연결 없음</option>
              {allRehearsals.map(r => (
                <option key={r.id} value={r.id}>{r.date} {r.title}</option>
              ))}
            </select>
          )}
          <FileUpload onUpload={handleUpload} />
        </div>

        {/* Media Player for selected file */}
        {selectedMedia && (
          <div className="inline-player-wrapper">
            <button className="close-player-btn" onClick={() => setSelectedMedia(null)}>&times;</button>
            <MediaPlayer file={selectedMedia} />
            <CommentSection targetType="media" targetId={selectedMedia.id} />
          </div>
        )}

        {/* Media List */}
        {song.media?.length > 0 ? (
          <div className="media-list">
            {song.media.map((media) => {
              const type = getMediaType(media);
              const isRenaming = renamingMediaId === media.id;

              return (
                <div key={media.id} className="media-item">
                  <span className="media-icon">{iconForType(media)}</span>

                  <div className="media-info">
                    {isRenaming ? (
                      <div className="rename-input-group">
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
                      <span className="media-name" onClick={() => handleStartRename(media)} title="클릭하여 이름 변경">
                        {getDisplayName(media.filename)} <span className="rename-hint">✏️</span>
                      </span>
                    )}
                    <span className="media-size">
                      {(media.file_size / 1024 / 1024).toFixed(2)} MB
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
                        <span className="rehearsal-badge" onClick={() => setRehearsalPickerMediaId(media.id)}>
                          📅 {media.rehearsal_date} {media.rehearsal_title}
                        </span>
                      ) : allRehearsals.length > 0 ? (
                        <button className="link-rehearsal-btn" onClick={() => setRehearsalPickerMediaId(media.id)}>
                          📅 합주 연결
                        </button>
                      ) : null
                    )}
                  </div>

                  {/* Action buttons based on file type */}
                  {!isRenaming && (
                    <>
                      {(type === 'audio' || type === 'video') && (
                        <button className="play-btn" onClick={() => handlePlay(media)}>▶ 재생</button>
                      )}
                      {type === 'image' && (
                        <button className="play-btn" onClick={() => handlePreview(media)}>🖼️ 보기</button>
                      )}
                      {type === 'document' && (
                        <a href={media.url} target="_blank" rel="noreferrer" className="play-btn">📄 다운로드</a>
                      )}

                      <button className="log-delete-btn" onClick={() => handleDeleteMedia(media.id)}>🗑️</button>
                    </>
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
