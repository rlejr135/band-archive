import React, { useState, useEffect } from 'react';
import { fetchSuggestions, createSuggestion, deleteSuggestion, voteSuggestion } from '../../services/api';
import PasswordModal from '../common/PasswordModal';
import './SongSuggestion.css';

const getYoutubeId = (url) => {
  if (!url) return null;
  const match = url.match(/(?:v=|youtu\.be\/|shorts\/)([\w-]+)/);
  return match ? match[1] : null;
};

const SongSuggestion = () => {
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ title: '', artist: '', link: '', memo: '' });
  const [deleteTarget, setDeleteTarget] = useState(null);

  useEffect(() => {
    loadSuggestions();
  }, []);

  const loadSuggestions = async () => {
    try {
      const data = await fetchSuggestions();
      setSuggestions(data);
    } catch (error) {
      console.error('Failed to load suggestions:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      await createSuggestion(form);
      setForm({ title: '', artist: '', link: '', memo: '' });
      setShowForm(false);
      await loadSuggestions();
    } catch (error) {
      console.error('Failed to create suggestion:', error);
    }
  };

  const handleVote = async (id, voteType) => {
    try {
      const updated = await voteSuggestion(id, voteType);
      setSuggestions(prev =>
        prev.map(s => s.id === id ? updated : s)
          .sort((a, b) => (b.thumbs_up - b.thumbs_down) - (a.thumbs_up - a.thumbs_down))
      );
    } catch (error) {
      console.error('Failed to vote:', error);
    }
  };

  const handleCheckPassword = async (password) => {
    if (!deleteTarget) return false;
    try {
      await deleteSuggestion(deleteTarget, password);
      return true;
    } catch (error) {
      console.error('Failed to delete suggestion:', error);
      return false;
    }
  };

  const handleDeleteSuccess = () => {
    setSuggestions(prev => prev.filter(s => s.id !== deleteTarget));
    setDeleteTarget(null);
  };

  if (loading) {
    return <div className="song-suggestion"><div className="loading">로딩 중...</div></div>;
  }

  return (
    <div className="song-suggestion">
      <div className="suggestion-header">
        <h2>다음 곡 추천</h2>
        <button className="primary-btn" onClick={() => setShowForm(!showForm)}>
          {showForm ? '취소' : '+ 추천 추가'}
        </button>
      </div>

      {showForm && (
        <form className="suggestion-form fade-in" onSubmit={handleSubmit}>
          <input
            type="text"
            placeholder="노래 제목"
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            required
          />
          <input
            type="text"
            placeholder="아티스트"
            value={form.artist}
            onChange={(e) => setForm({ ...form, artist: e.target.value })}
            required
          />
          <input
            type="url"
            placeholder="원곡 링크 (YouTube 등)"
            value={form.link}
            onChange={(e) => setForm({ ...form, link: e.target.value })}
            required
          />
          <textarea
            placeholder="메모 (선택사항)"
            value={form.memo}
            onChange={(e) => setForm({ ...form, memo: e.target.value })}
            rows={2}
          />
          <button type="submit" className="primary-btn">추천하기</button>
        </form>
      )}

      {suggestions.length === 0 ? (
        <div className="suggestion-empty">
          <p>아직 추천된 곡이 없습니다. 첫 번째 추천을 추가해보세요!</p>
        </div>
      ) : (
        <div className="suggestion-list">
          {suggestions.map((s, index) => (
            <div key={s.id} className="suggestion-card fade-in" style={{ animationDelay: `${index * 0.05}s` }}>
              <div className="suggestion-info">
                <div className="suggestion-rank">#{index + 1}</div>
                <div className="suggestion-details">
                  <h3>{s.title}</h3>
                  <p className="suggestion-artist">{s.artist}</p>
                  {s.memo && <p className="suggestion-memo">{s.memo}</p>}
                </div>
                {s.link && !getYoutubeId(s.link) && (
                  <a
                    href={s.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="suggestion-link"
                    title="원곡 듣기"
                  >
                    🔗
                  </a>
                )}
              </div>
              {s.link && getYoutubeId(s.link) && (
                <div className="suggestion-youtube">
                  <iframe
                    src={`https://www.youtube.com/embed/${getYoutubeId(s.link)}`}
                    title="YouTube video"
                    allowFullScreen
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  />
                </div>
              )}
              <div className="suggestion-actions">
                <button
                  className="vote-btn vote-up"
                  onClick={() => handleVote(s.id, 'up')}
                  title="추천"
                >
                  👍 {s.thumbs_up}
                </button>
                <span className="vote-score">
                  {s.thumbs_up - s.thumbs_down >= 0 ? '+' : ''}{s.thumbs_up - s.thumbs_down}
                </span>
                <button
                  className="vote-btn vote-down"
                  onClick={() => handleVote(s.id, 'down')}
                  title="비추천"
                >
                  👎 {s.thumbs_down}
                </button>
                <button
                  className="delete-btn"
                  onClick={() => setDeleteTarget(s.id)}
                  title="삭제"
                >
                  🗑️
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <PasswordModal
        isOpen={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteSuccess}
        checkPassword={handleCheckPassword}
        title="추천 삭제 (비밀번호: admin)"
      />
    </div>
  );
};

export default SongSuggestion;
