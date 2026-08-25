import React, { useState } from 'react';
import PasswordModal from '../common/PasswordModal';
import './SongList.css';

const SongList = ({ songs, onSelectSong, onDeleteSong, onAddSong }) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [songToDelete, setSongToDelete] = useState(null);

  const handleDeleteClick = (song) => {
    setSongToDelete(song);
    setIsModalOpen(true);
  };

  const handleConfirmDelete = () => {
    if (songToDelete) {
      onDeleteSong(songToDelete.id);
      setSongToDelete(null);
    }
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setSongToDelete(null);
  };

  return (
    <div className="song-list">
      <div className="song-list-header">
        <h2>곡 목록</h2>
      </div>
      
      {songs.length > 0 ? (
        <ul className="song-list-ul">
          {songs.map((song) => (
            <li key={song.id} className="song-item">
              <button
                type="button"
                onClick={() => onSelectSong(song)}
                className="song-title-button"
                aria-label={`${song.title} 상세 보기`}
              >
                {song.title} <span className="song-artist-span">- {song.artist}</span>
              </button>
              <button type="button" onClick={() => handleDeleteClick(song)} className="delete-btn" title="삭제" aria-label={`${song.title} 삭제`}>×</button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="empty-state-box">등록된 곡이 없습니다.</div>
      )}

      <button className="add-song-btn" onClick={onAddSong}>
        + 새 곡 추가
      </button>

      <PasswordModal
        isOpen={isModalOpen}
        onClose={handleCloseModal}
        onConfirm={handleConfirmDelete}
        title="곡 삭제 확인"
      />
    </div>
  );
};

export default SongList;

