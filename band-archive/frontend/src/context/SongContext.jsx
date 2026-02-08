import React, { createContext, useContext, useState, useEffect } from 'react';
import { fetchSongs, getSong, createSong, updateSong, deleteSong, uploadMedia, deleteMedia } from '../services/api';

const SongContext = createContext();

export const useSongs = () => useContext(SongContext);

export const SongProvider = ({ children }) => {
  const [songs, setSongs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentSong, setCurrentSong] = useState(null);
  const [isEditing, setIsEditing] = useState(false);

  useEffect(() => {
    loadSongs();
  }, []);

  const loadSongs = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await fetchSongs();
      setSongs(data);
    } catch (err) {
      console.error('Failed to load songs:', err);
      setError('곡 목록을 불러오는 데 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  const addSong = async (songData) => {
    const newSong = await createSong(songData);
    setSongs([...songs, newSong]);
    return newSong;
  };

  const editSong = async (id, songData) => {
      const updated = await updateSong(id, songData);
      setSongs(songs.map(s => s.id === id ? updated : s));
      if (currentSong && currentSong.id === id) {
        setCurrentSong(updated);
      }
      return updated;
  }

  const removeSong = async (id) => {
      await deleteSong(id);
      setSongs(songs.filter(s => s.id !== id));
      if (currentSong && currentSong.id === id) {
          setCurrentSong(null);
      }
  }

  const addMediaToSong = async (songId, file, onProgress) => {
      try {
        await uploadMedia(songId, file, onProgress);
        // Refresh the song data from backend after upload
        const updatedSong = await getSong(songId);
        const updatedSongs = songs.map(song =>
          song.id === songId ? updatedSong : song
        );
        setSongs(updatedSongs);

        // Update current song if it's the one being updated
        if (currentSong && currentSong.id === songId) {
          setCurrentSong(updatedSong);
        }
      } catch (error) {
        console.error('Failed to upload media:', error);
        throw error;
      }
  };

  const removeMediaFromSong = async (songId, mediaId) => {
    try {
      await deleteMedia(mediaId);
      // Refresh the song data from backend after deletion
      const updatedSong = await getSong(songId);
      const updatedSongs = songs.map(song =>
        song.id === songId ? updatedSong : song
      );
      setSongs(updatedSongs);

      // Update current song if it's the one being updated
      if (currentSong && currentSong.id === songId) {
        setCurrentSong(updatedSong);
      }
    } catch (error) {
      console.error('Failed to delete media:', error);
      throw error;
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
        addMediaToSong,
        removeMediaFromSong
    }}>
      {children}
    </SongContext.Provider>
  );
};
