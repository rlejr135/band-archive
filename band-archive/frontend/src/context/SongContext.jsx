import React, { createContext, useCallback, useContext, useState, useEffect, useRef } from 'react';
import { fetchSongs, getSong, createSong, updateSong, deleteSong, deleteMedia, renameMedia, voteMedia } from '../services/api';
import { isMediaVoteSnapshot, normalizeMediaVote, replaceMediaInSong, replaceMediaInSongs, sortSongMediaByScore, toggleMediaVote, voteStatePending, voteStateSettled } from '../services/mediaVoting.js';
import { createMediaVoteChannel } from '../services/mediaVoteChannel.js';

const SongContext = createContext();

export const useSongs = () => useContext(SongContext);

export const SongProvider = ({ children }) => {
  const [songs, setSongs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentSong, setCurrentSong] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
  const [voteStates, setVoteStates] = useState({});
  const mediaVoteChannelRef = useRef(null);
  const songsRequestRef = useRef(null);

  useEffect(() => {
    const channel = createMediaVoteChannel();
    if (!channel) return undefined;
    mediaVoteChannelRef.current = channel;
    const unsubscribe = channel.subscribe((updatedMedia) => {
      setSongs((previous) => replaceMediaInSongs(previous, updatedMedia));
      setCurrentSong((previous) => replaceMediaInSong(previous, updatedMedia));
    });
    return () => {
      unsubscribe();
      channel.close();
      if (mediaVoteChannelRef.current === channel) mediaVoteChannelRef.current = null;
    };
  }, []);

  const loadSongs = useCallback(() => {
    if (songsRequestRef.current) return songsRequestRef.current;
    const request = (async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await fetchSongs();
        setSongs(data.map(sortSongMediaByScore));
      } catch (err) {
        console.error('Failed to load songs:', err);
        setError('곡 목록을 불러오는 데 실패했습니다.');
      } finally {
        setLoading(false);
        songsRequestRef.current = null;
      }
    })();
    songsRequestRef.current = request;
    return request;
  }, []);

  useEffect(() => {
    loadSongs();
  }, [loadSongs]);

  const addSong = async (songData) => {
    const newSong = await createSong(songData);
    const result = sortSongMediaByScore(newSong);
    setSongs((previous) => [...previous, result]);
    return result;
  };

  const editSong = async (id, songData) => {
    const updated = await updateSong(id, songData);
    const result = sortSongMediaByScore(updated);
    setSongs((previous) => previous.map((song) => (song.id === id ? result : song)));
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
      const updatedSong = sortSongMediaByScore(await getSong(songId));
      setSongs((previous) => previous.map((song) => (song.id === songId ? updatedSong : song)));

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
      const updatedSong = sortSongMediaByScore(await getSong(songId));
      setSongs((previous) => previous.map((song) => (song.id === songId ? updatedSong : song)));

      // Update current song if it's the one being updated
      setCurrentSong((previous) => (previous?.id === songId ? updatedSong : previous));
    } catch (error) {
      console.error('Failed to rename media:', error);
      throw error;
    }
  };

  const refreshSong = async (songId) => {
    const updatedSong = sortSongMediaByScore(await getSong(songId));
    setSongs((previous) => previous.map((song) => (song.id === songId ? updatedSong : song)));
    setCurrentSong((previous) => (previous?.id === songId ? updatedSong : previous));
  };

  const voteForMedia = async (mediaId, requestedVote) => {
    const media = currentSong?.media?.find((item) => item.id === mediaId)
      || songs.flatMap((song) => song.media || []).find((item) => item.id === mediaId);
    if (!media) return null;

    const expectedVote = normalizeMediaVote(media.viewer_vote);
    const nextVote = toggleMediaVote(expectedVote, requestedVote);
    setVoteStates((previous) => voteStatePending(previous, mediaId));
    try {
      const updatedMedia = await voteMedia(mediaId, nextVote, expectedVote);
      if (!isMediaVoteSnapshot(updatedMedia)) throw new Error('Invalid media vote response');
      setSongs((previous) => replaceMediaInSongs(previous, updatedMedia));
      setCurrentSong((previous) => replaceMediaInSong(previous, updatedMedia));
      setVoteStates((previous) => voteStateSettled(previous, mediaId));
      mediaVoteChannelRef.current?.publish(updatedMedia);
      return updatedMedia;
    } catch (error) {
      const conflictMedia = error?.status === 409 && error?.payload?.code === 'vote_conflict'
        ? (error.payload.media || error.payload)
        : null;
      if (conflictMedia?.id === mediaId && isMediaVoteSnapshot(conflictMedia)) {
        setSongs((previous) => replaceMediaInSongs(previous, conflictMedia));
        setCurrentSong((previous) => replaceMediaInSong(previous, conflictMedia));
        setVoteStates((previous) => voteStateSettled(
          previous,
          mediaId,
          '다른 화면에서 투표가 변경되어 최신 상태로 갱신했습니다. 다시 눌러주세요.',
        ));
        return null;
      }
      setVoteStates((previous) => voteStateSettled(previous, mediaId, '투표를 저장하지 못했습니다. 다시 시도하세요.'));
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
        voteForMedia,
        voteStates,
    }}>
      {children}
    </SongContext.Provider>
  );
};
