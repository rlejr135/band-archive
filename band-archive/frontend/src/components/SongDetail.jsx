import React from 'react';
import './SongDetail.css';
import './SongMedia.css';

const SongDetail = ({ song, onEdit, onUploadMedia }) => {
  if (!song) {
    return <div className="song-detail-placeholder">곡을 선택해주세요.</div>;
  }

  const handleFileChange = (e) => {
    const files = Array.from(e.target.files);
    files.forEach(file => {
        onUploadMedia(song.id, file);
    });
    // Clear input
    e.target.value = '';
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
        <div className="media-header">
            <h4>미디어 파일</h4>
            <label className="upload-btn">
                + 파일 추가
                <input 
                    type="file" 
                    multiple 
                    accept="audio/*,video/*" 
                    onChange={handleFileChange}
                    style={{display: 'none'}}
                />
            </label>
        </div>
        
        {song.media && song.media.length > 0 ? (
            <ul className="media-list">
                {song.media.map((file, index) => (
                    <li key={index} className="media-item">
                        <span className="media-icon">{file.type === 'video' ? '🎬' : '🎵'}</span>
                        <div className="media-info">
                            <span className="media-name">{file.name}</span>
                        </div>
                        <button className="play-btn" onClick={() => alert(`Playing ${file.name}`)}>▶ 재생</button>
                    </li>
                ))}
            </ul>
        ) : (
            <div className="empty-media">
                <p>등록된 미디어 파일이 없습니다.</p>
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
