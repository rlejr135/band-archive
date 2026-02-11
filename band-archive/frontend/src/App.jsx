import React, { useState, useEffect } from 'react';
import { Routes, Route, useNavigate, useLocation, useParams, Navigate } from 'react-router-dom';
import { SongProvider, useSongs } from './context/SongContext';
import SongList from './components/SongList';
import SongDetail from './components/SongDetail';
import SongForm from './components/SongForm';
import SearchBar from './components/SearchBar';
import Dashboard from './components/Dashboard';
import SongSuggestion from './components/SongSuggestion';
import MemberDashboard from './components/MemberDashboard';
import MemberDetail from './components/MemberDetail';
import './App.css';
import './components/Header.css';

import logo from './assets/logo.png';

// Component to handle Song List + Detail view logic
const SongPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const { 
    songs, loading, error, currentSong, isEditing, 
    selectSong, removeSong, startCreate, startEdit, 
    addMediaToSong, loadSongs, cancelEdit, addSong, editSong 
  } = useSongs();
  
  const [searchQuery, setSearchQuery] = useState('');

  // Sync URL params with Context state
  useEffect(() => {
    if (id && songs.length > 0) {
      const songId = parseInt(id);
      if (!currentSong || currentSong.id !== songId) {
        const song = songs.find(s => s.id === songId);
        if (song) {
            selectSong(song);
        }
      }
    } else if (!id && !isEditing) {
      // If no ID in URL and not editing (e.g. creating new), clear selection
      if (currentSong) selectSong(null);
    }
  }, [id, songs, currentSong, selectSong, isEditing]);

  const filteredSongs = songs.filter(song => 
    song.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
    song.artist.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleSelectSong = (song) => {
    navigate(`/songs/${song.id}`);
  };

  const handleBackToGenerals = () => {
    selectSong(null);
    cancelEdit();
    navigate('/songs');
  };

  const handleSave = async (data) => {
    if (currentSong && currentSong.id) {
      await editSong(currentSong.id, data);
      navigate(`/songs/${currentSong.id}`); // Stay on detail
    } else {
      const newSong = await addSong(data);
      navigate(`/songs/${newSong.id}`);
    }
    cancelEdit();
  };
  
  const handleDelete = async (id) => {
      await removeSong(id);
      navigate('/songs');
  };

  return (
    <>
      <aside className="sidebar">
        <SearchBar onSearch={setSearchQuery} />
        {loading ? (
          <div className="loading">로딩 중...</div>
        ) : error ? (
          <div className="loading">
            <p>{error}</p>
            <button className="secondary-btn" onClick={loadSongs}>다시 시도</button>
          </div>
        ) : (
          <SongList
            songs={filteredSongs}
            onSelectSong={handleSelectSong}
            onDeleteSong={handleDelete}
            onAddSong={() => {
                startCreate();
                navigate('/songs/new'); // Optional: URL for new song
            }}
          />
        )}
      </aside>

      <section className="content-area">
        {isEditing ? (
          <SongForm
            song={currentSong} // If creating, this is null/undefined
            onSave={handleSave}
            onCancel={handleBackToGenerals}
          />
        ) : currentSong ? (
          <SongDetail
            song={currentSong}
            onEdit={startEdit}
            onUploadMedia={addMediaToSong}
            onBack={handleBackToGenerals}
          />
        ) : (
          <div className="empty-state">
            <p>곡을 선택하거나 새로운 곡을 추가하세요.</p>
            <button className="secondary-btn" onClick={() => {
                startCreate();
                // We don't necessarily need a route for 'new', can just set state
            }}>곡 추가하기</button>
          </div>
        )}
      </section>
    </>
  );
};


const MainContent = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { currentSong, isEditing } = useSongs();

  // Determine active view for styling
  const getCurrentView = () => {
      if (location.pathname === '/') return 'dashboard';
      if (location.pathname.startsWith('/songs')) return 'songs';
      if (location.pathname === '/suggestions') return 'suggestions';
      if (location.pathname.startsWith('/members')) return 'members';
      return '';
  };
  
  const currentView = getCurrentView();
  
  // Mobile responsive helper class
  // Check if we are in a detail view (URL has ID or isEditing) to toggle sidebar on mobile
  const hasSelectedSong = (location.pathname.startsWith('/songs/') && location.pathname !== '/songs/new' && location.pathname !== '/songs') || isEditing || currentSong;

  return (
    <div className="app-container fade-in">
      <header className="app-header">
        <div className="logo-container" onClick={() => navigate('/')} style={{cursor: 'pointer'}}>
            <img src={logo} alt="들뜬 Logo" className="band-logo" />
            <h1>들뜬 <span className="archive-text">Archive</span></h1>
        </div>
        <div className="header-actions">
          <button 
            className={`nav-btn ${currentView === 'dashboard' ? 'active' : ''}`}
            onClick={() => navigate('/')}
          >
            📊 대시보드
          </button>
          <button
            className={`nav-btn ${currentView === 'songs' ? 'active' : ''}`}
            onClick={() => navigate('/songs')}
          >
            🎵 곡 목록
          </button>
          <button
            className={`nav-btn ${currentView === 'members' ? 'active' : ''}`}
            onClick={() => navigate('/members')}
          >
            👥 멤버
          </button>
          <button
            className={`nav-btn ${currentView === 'suggestions' ? 'active' : ''}`}
            onClick={() => navigate('/suggestions')}
          >
            🗳️ 다음 곡 추천
          </button>
        </div>
      </header>

      {/* Main className handles mobile view switching */}
      <main className={`app-main ${hasSelectedSong ? 'has-selected-song' : ''}`}>
        <Routes>
            <Route path="/" element={<Dashboard onSelectSong={(song) => navigate(`/songs/${song.id}`)} onViewSongs={() => navigate('/songs')} />} />
            <Route path="/songs" element={<SongPage />} />
            <Route path="/songs/:id" element={<SongPage />} />
            <Route path="/suggestions" element={<SongSuggestion />} />
            <Route path="/members" element={<MemberDashboard />} />
            <Route path="/members/:id" element={<MemberDetail />} />
            <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
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
