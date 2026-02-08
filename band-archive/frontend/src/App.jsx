import React, { useState } from 'react';
import { SongProvider, useSongs } from './context/SongContext';
import SongList from './components/SongList';
import SongDetail from './components/SongDetail';
import SongForm from './components/SongForm';
import SearchBar from './components/SearchBar';
import Dashboard from './components/Dashboard';
import './App.css';
import './components/Header.css';

import logo from './assets/logo.png';

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
  const [currentView, setCurrentView] = useState('dashboard'); // 'dashboard' or 'songs'

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
      const newSong = await addSong(data);
      selectSong(newSong);
    }
    cancelEdit();
  };

  const handleDashboardSongSelect = (song) => {
    setCurrentView('songs');
    selectSong(song);
  };

  return (
    <div className="app-container fade-in">
      <header className="app-header">
        <div className="logo-container">
            <img src={logo} alt="들뜬 Logo" className="band-logo" />
            <h1>들뜬 <span className="archive-text">Archive</span></h1>
        </div>
        <div className="header-actions">
          <button 
            className={`nav-btn ${currentView === 'dashboard' ? 'active' : ''}`}
            onClick={() => setCurrentView('dashboard')}
          >
            📊 대시보드
          </button>
          <button 
            className={`nav-btn ${currentView === 'songs' ? 'active' : ''}`}
            onClick={() => setCurrentView('songs')}
          >
            🎵 곡 목록
          </button>
          <button className="primary-btn" onClick={startCreate}>
             + 새 곡 추가
          </button>
        </div>
      </header>

      <main className="app-main">
        {currentView === 'dashboard' ? (
          <Dashboard onSelectSong={handleDashboardSongSelect} />
        ) : (
          <>
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
          </>
        )}
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
