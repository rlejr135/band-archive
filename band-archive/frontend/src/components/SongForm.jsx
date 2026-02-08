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
      setFormData({
        title: song.title || '',
        artist: song.artist || '',
        memo: song.memo || '',
        id: song.id 
      });
    } else {
        setFormData({
            title: '',
            artist: '',
            memo: ''
        });
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
      <h2>{song ? '곡 정보 수정' : '새 곡 추가'}</h2>
      <div className="form-group">
        <label>제목</label>
        <input name="title" value={formData.title} onChange={handleChange} required placeholder="곡 제목을 입력하세요" />
      </div>
      <div className="form-group">
        <label>아티스트</label>
        <input name="artist" value={formData.artist} onChange={handleChange} required placeholder="아티스트 이름을 입력하세요" />
      </div>
      <div className="form-group">
        <label>메모</label>
        <textarea name="memo" value={formData.memo} onChange={handleChange} rows="5" placeholder="필요한 메모를 남겨주세요" />
      </div>
      <div className="form-actions">
        <button type="submit" className="save-btn">저장</button>
        <button type="button" onClick={onCancel} className="cancel-btn">취소</button>
      </div>
    </form>
  );
};

export default SongForm;
