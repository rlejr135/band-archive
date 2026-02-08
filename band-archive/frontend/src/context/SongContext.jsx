import React, { createContext, useContext, useState, useEffect } from 'react';
import { fetchSongs, createSong, updateSong, deleteSong, uploadMedia } from '../services/api';

const SongContext = createContext();

export const useSongs = () => useContext(SongContext);

export const SongProvider = ({ children }) => {
  const [songs, setSongs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [currentSong, setCurrentSong] = useState(null);
  const [isEditing, setIsEditing] = useState(false); // Mode: 'list', 'detail', 'create', 'edit'

  useEffect(() => {
    loadSongs();
  }, []);

  const loadSongs = async () => {
    setLoading(true);
    const data = await fetchSongs();
    setSongs(data);
    setLoading(false);
  };

  const addSong = async (songData) => {
    const newSong = await createSong(songData);
    setSongs([...songs, newSong]);
    return newSong;
  };

  const editSong = async (id, songData) => {
      const updated = await updateSong(id, songData);
      setSongs(songs.map(s => s.id === id ? updated : s));
      return updated;
  }

  const removeSong = async (id) => {
      await deleteSong(id);
      setSongs(songs.filter(s => s.id !== id));
      if (currentSong && currentSong.id === id) {
          setCurrentSong(null);
      }
  }

  const addMediaToSong = async (songId, file) => {
      const mediaItem = await uploadMedia(songId, file);
      // Update local state
      const updatedSongs = songs.map(song => {
          if (song.id === songId) {
              const updatedSong = { ...song, media: [...(song.media || []), mediaItem] };
              if (currentSong && currentSong.id === songId) {
                  setCurrentSong(updatedSong);
              }
              return updatedSong;
          }
          return song;
      });
      setSongs(updatedSongs);
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
      // If we were creating a new song, go back to list (currentSong is null)
      // If we were editing, go back to detail (currentSong is set)
  }

  return (
    <SongContext.Provider value={{ 
        songs, 
        loading, 
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
        addMediaToSong
    }}>
      {children}
    </SongContext.Provider>
  );
};
