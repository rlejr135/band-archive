import React, { createContext, useContext, useState, useEffect } from 'react';
import { fetchSongs, getSong, createSong, updateSong, deleteSong, deleteMedia, renameMedia, voteSong } from '../services/api';
import { normalizeSongVote, replaceSongAndSort, sortSongsByScore, toggleSongVote, voteStatePending, voteStateSettled } from '../services/songVoting.js';

const SongContext = createContext();

export const useSongs = () => useContext(SongContext);

export const SongProvider = ({ children }) => {
  const [songs, setSongs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentSong, setCurrentSong] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
  const [voteStates, setVoteStates] = useState({});

  useEffect(() => {
    loadSongs();
  }, []);

  const loadSongs = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await fetchSongs();
      setSongs(sortSongsByScore(data));
    } catch (err) {
      console.error('Failed to load songs:', err);
      setError('곡 목록을 불러오는 데 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  const addSong = async (songData) => {
    const newSong = await createSong(songData);
    setSongs((previous) => sortSongsByScore([...previous, newSong]));
    return newSong;
  };

  const editSong = async (id, songData) => {
    const updated = await updateSong(id, songData);
    const existing = songs.find((song) => song.id === id) || currentSong;
    const result = { ...updated, viewer_vote: normalizeSongVote(existing?.viewer_vote) };
    setSongs((previous) => replaceSongAndSort(previous, result));
    setCurrentSong((previous) => (previous?.id === id ? result : previous));
    return result;
  }

  const removeSong = async (id) => {
      await deleteSong(id);
      setSongs((previous) => previous.filter((song) => song.id !== id));
      setCurrentSong((previous) => (previous?.id === id ? null : previous));
  }

  const removeMediaFromSong = async (songId, mediaId) => {
    try {
      await deleteMedia(mediaId);
      // Refresh the song data from backend after deletion
      const updatedSong = await getSong(songId);
      setSongs((previous) => replaceSongAndSort(previous, updatedSong));

      // Update current song if it's the one being updated
      setCurrentSong((previous) => (previous?.id === songId ? updatedSong : previous));
    } catch (error) {
      console.error('Failed to delete media:', error);
      throw error;
    }
  };

  const renameMediaInSong = async (songId, mediaId, newName) => {
    try {
      await renameMedia(mediaId, newName);
      // Refresh the song data from backend after rename
      const updatedSong = await getSong(songId);
      setSongs((previous) => replaceSongAndSort(previous, updatedSong));

      // Update current song if it's the one being updated
      setCurrentSong((previous) => (previous?.id === songId ? updatedSong : previous));
    } catch (error) {
      console.error('Failed to rename media:', error);
      throw error;
    }
  };

  const refreshSong = async (songId) => {
    const updatedSong = await getSong(songId);
    setSongs((previous) => replaceSongAndSort(previous, updatedSong));
    setCurrentSong((previous) => (previous?.id === songId ? updatedSong : previous));
  };

  const voteForSong = async (songId, requestedVote) => {
    const song = songs.find((item) => item.id === songId) || currentSong;
    if (!song || song.id !== songId) return null;

    const nextVote = toggleSongVote(song.viewer_vote, requestedVote);
    setVoteStates((previous) => voteStatePending(previous, songId));
    try {
      const updatedSong = await voteSong(songId, nextVote);
      setSongs((previous) => replaceSongAndSort(previous, updatedSong));
      setCurrentSong((previous) => (previous?.id === songId ? updatedSong : previous));
      setVoteStates((previous) => voteStateSettled(previous, songId));
      return updatedSong;
    } catch {
      setVoteStates((previous) => voteStateSettled(previous, songId, '투표를 저장하지 못했습니다. 다시 시도하세요.'));
      return null;
    }
  };

  const selectSong = (song) => {
    setCurrentSong(song);
    setIsEditing(false);
  };

  const startEdit = (song) => {
      setCurrentSong(song);
      setIsEditing(true);
  }

  const startCreate = () => {
      setCurrentSong(null);
      setIsEditing(true);
  }

  const cancelEdit = () => {
      setIsEditing(false);
  }

  return (
    <SongContext.Provider value={{
        songs,
        loading,
        error,
        loadSongs,
        addSong,
        editSong,
        removeSong,
        currentSong,
        selectSong,
        isEditing,
        startEdit,
        startCreate,
        cancelEdit,
        removeMediaFromSong,
        renameMediaInSong,
        refreshSong,
        voteForSong,
        voteStates,
    }}>
      {children}
    </SongContext.Provider>
  );
};
