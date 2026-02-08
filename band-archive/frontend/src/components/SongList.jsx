import React, { useState } from 'react';
import PasswordModal from './PasswordModal';
import './SongList.css';

const SongList = ({ songs, onSelectSong, onDeleteSong }) => {
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
      <h2>곡 목록</h2>
      <ul>
        {songs.map((song) => (
          <li key={song.id} className="song-item">
            <span onClick={() => onSelectSong(song)}>
              {song.title} - {song.artist}
            </span>
            <button onClick={() => handleDeleteClick(song)} className="delete-btn">삭제</button>
          </li>
        ))}
      </ul>

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

