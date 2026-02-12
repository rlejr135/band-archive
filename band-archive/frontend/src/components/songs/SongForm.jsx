import React, { useState, useEffect } from 'react';
import './SongForm.css';

const STATUSES = [
  { value: 'Practice', label: '연습중' },
  { value: 'Completed', label: '완료' },
  { value: 'OnHold', label: '보류' },
];

const SongForm = ({ song, onSave, onCancel }) => {
  const [formData, setFormData] = useState({
    title: '',
    artist: '',
    status: 'Practice',
    genre: '',
    difficulty: 3,
    lyrics: '',
    chords: '',
    link: '',
    memo: '',
  });

  useEffect(() => {
    if (song) {
      setFormData({
        title: song.title || '',
        artist: song.artist || '',
        status: song.status || 'Practice',
        genre: song.genre || '',
        difficulty: song.difficulty || 3,
        lyrics: song.lyrics || '',
        chords: song.chords || '',
        link: song.link || '',
        memo: song.memo || '',
      });
    } else {
      setFormData({
        title: '',
        artist: '',
        status: 'Practice',
        genre: '',
        difficulty: 3,
        lyrics: '',
        chords: '',
        link: '',
        memo: '',
      });
    }
  }, [song]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: name === 'difficulty' ? parseInt(value, 10) : value
    }));
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    onSave(formData);
  };

  return (
    <form className="song-form" onSubmit={handleSubmit}>
      <h2>{song ? '곡 정보 수정' : '새 곡 추가'}</h2>

      <div className="form-row">
        <div className="form-group">
          <label>제목</label>
          <input name="title" value={formData.title} onChange={handleChange} required placeholder="곡 제목을 입력하세요" />
        </div>
        <div className="form-group">
          <label>아티스트</label>
          <input name="artist" value={formData.artist} onChange={handleChange} required placeholder="아티스트 이름을 입력하세요" />
        </div>
      </div>

      <div className="form-row">
        <div className="form-group">
          <label>상태</label>
          <select name="status" value={formData.status} onChange={handleChange}>
            {STATUSES.map(s => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label>장르</label>
          <input name="genre" value={formData.genre} onChange={handleChange} placeholder="예: Rock, Pop, Jazz" />
        </div>
        <div className="form-group">
          <label>난이도 ({formData.difficulty})</label>
          <input type="range" name="difficulty" min="1" max="5" value={formData.difficulty} onChange={handleChange} />
        </div>
      </div>

      <div className="form-group">
        <label>링크</label>
        <input name="link" value={formData.link} onChange={handleChange} placeholder="YouTube, 악보 링크 등" />
      </div>

      <div className="form-group">
        <label>가사</label>
        <textarea name="lyrics" value={formData.lyrics} onChange={handleChange} rows="5" placeholder="가사를 입력하세요" />
      </div>

      <div className="form-group">
        <label>코드</label>
        <textarea name="chords" value={formData.chords} onChange={handleChange} rows="3" placeholder="코드 진행을 입력하세요" />
      </div>

      <div className="form-group">
        <label>메모</label>
        <textarea name="memo" value={formData.memo} onChange={handleChange} rows="3" placeholder="필요한 메모를 남겨주세요" />
      </div>

      <div className="form-actions">
        <button type="submit" className="save-btn">저장</button>
        <button type="button" onClick={onCancel} className="cancel-btn">취소</button>
      </div>
    </form>
  );
};

export default SongForm;
