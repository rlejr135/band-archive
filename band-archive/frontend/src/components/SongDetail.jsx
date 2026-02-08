import React, { useState } from 'react';
import { API_URL } from '../services/api';
import './SongDetail.css';
import './SongMedia.css';
import FileUpload from './FileUpload';
import MediaPlayer from './MediaPlayer';
import PracticeLogSection from './PracticeLogSection';

const SongDetail = ({ song, onEdit, onUploadMedia }) => {
  const [selectedMedia, setSelectedMedia] = useState(null);

  if (!song) {
    return <div className="song-detail-placeholder">곡을 선택해주세요.</div>;
  }

  const handleUpload = async (file, onProgress) => {
    await onUploadMedia(song.id, file, onProgress);
  };

  const handlePlayMedia = () => {
    if (song.sheet_music) {
      const fileUrl = `${API_URL}/uploads/${song.sheet_music}`;
      setSelectedMedia({
        name: song.sheet_music,
        url: fileUrl,
        type: song.sheet_music.match(/\.(mp4|webm|ogg|mov|avi|mkv)$/i) ? 'video' : 'audio'
      });
    }
  };

  const statusLabel = { Practice: '연습중', Completed: '완료', OnHold: '보류' };

  return (
    <div className="song-detail">
      <h2>{song.title}</h2>
      <h3>{song.artist}</h3>

      <div className="song-info">
        <p><strong>상태:</strong> {statusLabel[song.status] || song.status}</p>
        {song.genre && <p><strong>장르:</strong> {song.genre}</p>}
        {song.difficulty && <p><strong>난이도:</strong> {'★'.repeat(song.difficulty)}{'☆'.repeat(5 - song.difficulty)}</p>}
        {song.link && <p><strong>링크:</strong> <a href={song.link} target="_blank" rel="noreferrer">{song.link}</a></p>}
      </div>

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
              onClick={handlePlayMedia}
            >
              <span className="media-icon">
                {song.sheet_music.match(/\.(mp4|webm|ogg|mov|avi|mkv)$/i) ? '🎬' : '🎵'}
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

      {/* Practice Logs */}
      <PracticeLogSection songId={song.id} />

      <div className="detail-actions">
        <button onClick={() => onEdit(song)} className="edit-btn">정보 수정</button>
      </div>
    </div>
  );
};

export default SongDetail;
