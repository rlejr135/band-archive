import React, { useState } from 'react';
import { API_URL } from '../../services/api';
import { useSongs } from '../../context/SongContext';
import './SongDetail.css';
import './SongMedia.css';
import FileUpload from '../common/FileUpload';
import MediaPlayer from '../common/MediaPlayer';
import PracticeLogSection from '../practices/PracticeLogSection';

const SongDetail = ({ song, onEdit, onUploadMedia, onBack }) => {
  const [selectedMedia, setSelectedMedia] = useState(null);
  const { removeMediaFromSong, renameMediaInSong } = useSongs();
  
  const [renamingMediaId, setRenamingMediaId] = useState(null);
  const [newFilename, setNewFilename] = useState('');

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

  const handleUpload = async (file, onProgress) => {
    await onUploadMedia(song.id, file, onProgress);
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
      url: `${API_URL}${media.url}`,
      type: getMediaType(media),
    });
  };

  const handlePreview = (media) => {
    setSelectedMedia({
      id: media.id,
      name: getDisplayName(media.filename),
      url: `${API_URL}${media.url}`,
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

      {(song.lyrics || song.chords || song.memo) && (
        <div className="song-content">
          {song.lyrics && (
            <div className="song-lyrics">
              <h4>가사</h4>
              <pre>{song.lyrics}</pre>
            </div>
          )}
          {song.chords && (
            <div className="song-lyrics">
              <h4>코드</h4>
              <pre>{song.chords}</pre>
            </div>
          )}
          {song.memo && (
            <div className="song-memo">
              <h4>메모</h4>
              <pre>{song.memo}</pre>
            </div>
          )}
        </div>
      )}
      <div className="song-media">
        <h4>미디어 파일</h4>

        {/* Drag & Drop Upload */}
        <FileUpload onUpload={handleUpload} />

        {/* Media Player for selected file */}
        {selectedMedia && (
          <MediaPlayer file={selectedMedia} />
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
                    <span className="media-size">{(media.file_size / 1024 / 1024).toFixed(2)} MB</span>
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
                        <a href={`${API_URL}${media.url}`} target="_blank" rel="noreferrer" className="play-btn">📄 다운로드</a>
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

      {/* Practice Logs */}
      <PracticeLogSection songId={song.id} />

      <div className="detail-actions">
        <button onClick={() => onEdit(song)} className="edit-btn">정보 수정</button>
      </div>
    </div>
  );
};

export default SongDetail;
