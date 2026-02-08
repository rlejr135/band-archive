import React, { useState } from 'react';
import './SongDetail.css';
import './SongMedia.css';
import FileUpload from './FileUpload';
import MediaPlayer from './MediaPlayer';

const SongDetail = ({ song, onEdit, onUploadMedia }) => {
  const [selectedMedia, setSelectedMedia] = useState(null);

  if (!song) {
    return <div className="song-detail-placeholder">곡을 선택해주세요.</div>;
  }

  const handleUpload = async (file, onProgress) => {
    await onUploadMedia(song.id, file, onProgress);
  };

  return (
    <div className="song-detail">
      <h2>{song.title}</h2>
      <h3>{song.artist}</h3>
      
      <div className="song-memo">
        <h4>메모</h4>
        <pre>{song.memo || '메모가 없습니다.'}</pre>
      </div>

      <div className="song-media">
        <h4>미디어 파일</h4>
        
        {/* Drag & Drop Upload */}
        <FileUpload onUpload={handleUpload} />

        {/* Media Player for selected file */}
        {selectedMedia && (
          <MediaPlayer file={selectedMedia} />
        )}
        
        {/* Media List */}
        {song.sheet_music && (
          <div className="media-list">
            <div 
              className="media-item"
              onClick={() => setSelectedMedia({ 
                name: song.sheet_music, 
                sheet_music: song.sheet_music,
                type: song.sheet_music.match(/\.(mp4|webm|ogg|mov)$/i) ? 'video' : 'audio'
              })}
            >
              <span className="media-icon">
                {song.sheet_music.match(/\.(mp4|webm|ogg|mov)$/i) ? '🎬' : '🎵'}
              </span>
              <div className="media-info">
                <span className="media-name">{song.sheet_music}</span>
              </div>
              <button className="play-btn">▶ 재생</button>
            </div>
          </div>
        )}

        {!song.sheet_music && (
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
