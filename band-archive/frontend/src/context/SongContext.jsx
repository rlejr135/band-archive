import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { fetchSongs, getSong, createSong, updateSong, deleteSong, deleteMedia, renameMedia, voteSong } from '../services/api';
import { isSongVoteSnapshot, normalizeSongVote, replaceSongAndSort, replaceVoteSongAndSort, sameSongVoteSnapshot, sortSongsByScore, toggleSongVote, voteStatePending, voteStateSettled } from '../services/songVoting.js';
import { createSongVoteChannel } from '../services/songVoteChannel.js';

const SongContext = createContext();

export const useSongs = () => useContext(SongContext);

export const SongProvider = ({ children }) => {
  const [songs, setSongs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentSong, setCurrentSong] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
  const [voteStates, setVoteStates] = useState({});
  const voteChannelRef = useRef(null);

  useEffect(() => {
    loadSongs();
  }, []);

  useEffect(() => {
    const channel = createSongVoteChannel();
    if (!channel) return undefined;
    voteChannelRef.current = channel;
    const unsubscribe = channel.subscribe((updatedSong) => {
      setSongs((previous) => replaceVoteSongAndSort(previous, updatedSong));
      setCurrentSong((previous) => (
        previous?.id === updatedSong.id && !sameSongVoteSnapshot(previous, updatedSong)
          ? updatedSong
          : previous
      ));
    });
    return () => {
      unsubscribe();
      channel.close();
      if (voteChannelRef.current === channel) voteChannelRef.current = null;
    };
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

    const expectedVote = normalizeSongVote(song.viewer_vote);
    const nextVote = toggleSongVote(expectedVote, requestedVote);
    setVoteStates((previous) => voteStatePending(previous, songId));
    try {
      const updatedSong = await voteSong(songId, nextVote, expectedVote);
      setSongs((previous) => replaceSongAndSort(previous, updatedSong));
      setCurrentSong((previous) => (previous?.id === songId ? updatedSong : previous));
      setVoteStates((previous) => voteStateSettled(previous, songId));
      voteChannelRef.current?.publish(updatedSong);
      return updatedSong;
    } catch (error) {
      const conflictSong = error?.status === 409 && error?.payload?.code === 'vote_conflict'
        ? error.payload.song
        : null;
      if (conflictSong?.id === songId && isSongVoteSnapshot(conflictSong)) {
        setSongs((previous) => replaceSongAndSort(previous, conflictSong));
        setCurrentSong((previous) => (previous?.id === songId ? conflictSong : previous));
        setVoteStates((previous) => voteStateSettled(
          previous,
          songId,
          '다른 화면에서 투표가 변경되어 최신 상태로 갱신했습니다. 다시 눌러주세요.',
        ));
        return null;
      }
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
