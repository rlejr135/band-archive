import React from 'react';
import './SongList.css';

const SongList = ({ songs, onSelectSong, onDeleteSong }) => {
  return (
    <div className="song-list">
      <h2>곡 목록</h2>
      <ul>
        {songs.map((song) => (
          <li key={song.id} className="song-item">
            <span onClick={() => onSelectSong(song)}>
              {song.title} - {song.artist}
            </span>
            <button onClick={() => onDeleteSong(song.id)} className="delete-btn">삭제</button>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default SongList;
