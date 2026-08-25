import React, { useState } from 'react';
import PasswordModal from '../common/PasswordModal';
import { sortSongsByScore } from '../../services/songVoting.js';
import './SongList.css';

const SongList = ({ songs, onSelectSong, onDeleteSong, onAddSong, onVoteSong, voteStates = {} }) => {
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

  const handleVote = (event, song, vote) => {
    event.stopPropagation();
    onVoteSong?.(song.id, vote);
  };

  const orderedSongs = sortSongsByScore(songs);

  return (
    <div className="song-list">
      <div className="song-list-header">
        <h2>곡 목록</h2>
      </div>
      
      {orderedSongs.length > 0 ? (
        <ul className="song-list-ul">
          {orderedSongs.map((song) => {
            const voteState = voteStates[song.id] || {};
            const viewerVote = song.viewer_vote === 1 || song.viewer_vote === -1 ? song.viewer_vote : 0;
            const score = Number.isFinite(Number(song.vote_score)) ? Number(song.vote_score) : 0;
            return (
            <li key={song.id} className="song-item">
              <button
                type="button"
                onClick={() => onSelectSong(song)}
                className="song-title-button"
                aria-label={`${song.title} 상세 보기`}
              >
                {song.title} <span className="song-artist-span">- {song.artist}</span>
              </button>
              <div className="song-vote-controls" role="group" aria-label={`${song.title} 투표`}>
                <button
                  type="button"
                  className={`song-vote-btn vote-up ${viewerVote === 1 ? 'active' : ''}`}
                  onClick={(event) => handleVote(event, song, 1)}
                  disabled={voteState.loading}
                  aria-pressed={viewerVote === 1}
                  aria-label={`${song.title} 추천${viewerVote === 1 ? ' 취소' : ''}`}
                >
                  👍 {song.upvote_count ?? 0}
                </button>
                <span className="song-vote-score" aria-label={`점수 ${score}`}>{score > 0 ? '+' : ''}{score}</span>
                <button
                  type="button"
                  className={`song-vote-btn vote-down ${viewerVote === -1 ? 'active' : ''}`}
                  onClick={(event) => handleVote(event, song, -1)}
                  disabled={voteState.loading}
                  aria-pressed={viewerVote === -1}
                  aria-label={`${song.title} 비추천${viewerVote === -1 ? ' 취소' : ''}`}
                >
                  👎 {song.downvote_count ?? 0}
                </button>
                {voteState.error && <span className="song-vote-error" role="alert">{voteState.error}</span>}
              </div>
              <button type="button" onClick={() => handleDeleteClick(song)} className="delete-btn" title="삭제" aria-label={`${song.title} 삭제`}>×</button>
            </li>
            );
          })}
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

