import React from 'react';
import './SongDetail.css';

const SongDetail = ({ song, onEdit }) => {
  if (!song) {
    return <div className="song-detail-placeholder">곡을 선택해주세요.</div>;
  }

  return (
    <div className="song-detail">
      <h2>{song.title}</h2>
      <h3>{song.artist}</h3>
      <div className="song-info">
        <p><strong>장르:</strong> {song.genre}</p>
        <p><strong>난이도:</strong> {song.difficulty}</p>
      </div>
      <div className="song-lyrics">
        <h4>가사</h4>
        <pre>{song.lyrics}</pre>
      </div>
      <div className="song-memo">
        <h4>메모</h4>
        <pre>{song.memo}</pre>
      </div>
      <button onClick={() => onEdit(song)} className="edit-btn">수정</button>
    </div>
  );
};

export default SongDetail;
