import React, { useState } from 'react';
import { SongProvider, useSongs } from './context/SongContext';
import SongList from './components/SongList';
import SongDetail from './components/SongDetail';
import SongForm from './components/SongForm';
import SearchBar from './components/SearchBar';
import './App.css';
import './components/Header.css';

import logo from './assets/logo.png'; // Make sure to save the image as logo.png

// Main Content Component to use context
const MainContent = () => {
  const { 
    songs, 
    loading, 
    currentSong, 
    isEditing, 
    selectSong, 
    startCreate, 
    addSong, 
    editSong, 
    removeSong, 
    cancelEdit,
    startEdit,
    addMediaToSong
  } = useSongs();

  const [searchQuery, setSearchQuery] = useState('');

  // Filter songs
  const filteredSongs = songs.filter(song => 
    song.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
    song.artist.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleSearch = (query) => {
    setSearchQuery(query);
  };

  const handleSave = async (data) => {
    if (currentSong && currentSong.id) {
      await editSong(currentSong.id, data);
    } else {
      await addSong(data);
    }
    cancelEdit(); // Go back to view mode
  };

  return (
    <div className="app-container fade-in">
      <header className="app-header">
        <div className="logo-container">
            <img src={logo} alt="들뜬 Logo" className="band-logo" />
            <h1>들뜬 <span className="archive-text">Archive</span></h1>
        </div>
        <button className="primary-btn" onClick={startCreate}>
           + 새 곡 추가
        </button>
      </header>

      <main className="app-main">
        <aside className="sidebar">
          <SearchBar onSearch={handleSearch} />
          {loading ? (
            <div className="loading">로딩 중...</div>
          ) : (
            <SongList 
              songs={filteredSongs} 
              onSelectSong={selectSong} 
              onDeleteSong={removeSong} 
            />
          )}
        </aside>

        <section className="content-area">
          {isEditing ? (
            <SongForm 
              song={currentSong} 
              onSave={handleSave} 
              onCancel={cancelEdit} 
            />
          ) : currentSong ? (
            <SongDetail 
              song={currentSong} 
              onEdit={startEdit} 
              onUploadMedia={addMediaToSong}
            />
          ) : (
            <div className="empty-state">
              <p>곡을 선택하거나 새로운 곡을 추가하세요.</p>
              <button className="secondary-btn" onClick={startCreate}>곡 추가하기</button>
            </div>
          )}
        </section>
      </main>
    </div>
  );
};

function App() {
  return (
    <SongProvider>
      <MainContent />
    </SongProvider>
  );
}

export default App;
