import React, { useState, useEffect } from 'react';
import './SongForm.css';

const SongForm = ({ song, onSave, onCancel }) => {
  const [formData, setFormData] = useState({
    title: '',
    artist: '',
    genre: '',
    difficulty: 1,
    lyrics: '',
    memo: ''
  });

  useEffect(() => {
    if (song) {
      setFormData(song);
    }
  }, [song]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    onSave(formData);
  };

  return (
    <form className="song-form" onSubmit={handleSubmit}>
      <h2>{song ? '곡 수정' : '새 곡 추가'}</h2>
      <div className="form-group">
        <label>제목</label>
        <input name="title" value={formData.title} onChange={handleChange} required />
      </div>
      <div className="form-group">
        <label>아티스트</label>
        <input name="artist" value={formData.artist} onChange={handleChange} required />
      </div>
      <div className="form-group">
        <label>장르</label>
        <input name="genre" value={formData.genre} onChange={handleChange} />
      </div>
      <div className="form-group">
        <label>난이도 (1-5)</label>
        <input type="number" name="difficulty" min="1" max="5" value={formData.difficulty} onChange={handleChange} />
      </div>
      <div className="form-group">
        <label>가사</label>
        <textarea name="lyrics" value={formData.lyrics} onChange={handleChange} rows="5" />
      </div>
      <div className="form-group">
        <label>메모</label>
        <textarea name="memo" value={formData.memo} onChange={handleChange} rows="3" />
      </div>
      <div className="form-actions">
        <button type="submit" className="save-btn">저장</button>
        <button type="button" onClick={onCancel} className="cancel-btn">취소</button>
      </div>
    </form>
  );
};

export default SongForm;
