import React, { useState, useEffect } from 'react';
import { createRehearsal, updateRehearsal } from '../../services/rehearsalApi';
import './RehearsalModal.css';

const COLORS = ['#ffd32a', '#0fbcf9', '#ff5e57', '#0be881', '#f368e0', '#ff9f43'];

const toDateStr = (date) => {
  if (!date) return '';
  if (typeof date === 'string') return date;
  return date.toLocaleDateString('en-CA'); // YYYY-MM-DD
};

const RehearsalModal = ({ rehearsal, songs, defaultDate, onClose, onSave }) => {
  const isEdit = !!rehearsal;

  const [title, setTitle] = useState('');
  const [date, setDate] = useState('');
  const [usePeriod, setUsePeriod] = useState(false);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [time, setTime] = useState('');
  const [memo, setMemo] = useState('');
  const [color, setColor] = useState('#ffd32a');
  const [selectedSongIds, setSelectedSongIds] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isEdit) {
      setTitle(rehearsal.title || '');
      setDate(rehearsal.date || '');
      setTime(rehearsal.time || '');
      setMemo(rehearsal.memo || '');
      setColor(rehearsal.color || '#ffd32a');
      setSelectedSongIds(rehearsal.songs?.map((s) => s.id) || []);
      if (rehearsal.start_date && rehearsal.end_date) {
        setUsePeriod(true);
        setStartDate(rehearsal.start_date);
        setEndDate(rehearsal.end_date);
      }
    } else {
      setDate(toDateStr(defaultDate));
    }
  }, [rehearsal, defaultDate, isEdit]);

  const toggleSong = (songId) => {
    setSelectedSongIds((prev) =>
      prev.includes(songId) ? prev.filter((id) => id !== songId) : [...prev, songId]
    );
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!title.trim()) {
      setError('제목을 입력해주세요');
      return;
    }
    if (!date) {
      setError('날짜를 선택해주세요');
      return;
    }

    const data = {
      title: title.trim(),
      date,
      start_date: usePeriod ? startDate || null : null,
      end_date: usePeriod ? endDate || null : null,
      time: time || null,
      memo: memo.trim() || null,
      color,
      song_ids: selectedSongIds,
    };

    setSaving(true);
    try {
      if (isEdit) {
        await updateRehearsal(rehearsal.id, data);
      } else {
        await createRehearsal(data);
      }
      onSave();
    } catch (err) {
      setError('저장에 실패했습니다');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content rehearsal-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{isEdit ? '일정 수정' : '일정 추가'}</h3>
          <button className="modal-close-btn" onClick={onClose}>×</button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {/* 제목 */}
            <div className="form-group">
              <label>제목 *</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="예: 정기 합주, 공연 리허설"
                maxLength={200}
              />
            </div>

            {/* 날짜 */}
            <div className="form-group">
              <label>날짜 *</label>
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>

            {/* 기간 토글 */}
            <div className="form-group form-toggle">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  checked={usePeriod}
                  onChange={(e) => setUsePeriod(e.target.checked)}
                />
                <span>기간 설정</span>
              </label>
            </div>

            {usePeriod && (
              <div className="form-row">
                <div className="form-group">
                  <label>시작일</label>
                  <input
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label>종료일</label>
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                  />
                </div>
              </div>
            )}

            {/* 시간 */}
            <div className="form-group">
              <label>시간</label>
              <input
                type="time"
                value={time}
                onChange={(e) => setTime(e.target.value)}
              />
            </div>

            {/* 색상 */}
            <div className="form-group">
              <label>색상</label>
              <div className="color-picker">
                {COLORS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    className={`color-option ${color === c ? 'active' : ''}`}
                    style={{ backgroundColor: c }}
                    onClick={() => setColor(c)}
                  />
                ))}
              </div>
            </div>

            {/* 합주곡 선택 */}
            {songs.length > 0 && (
              <div className="form-group">
                <label>합주곡</label>
                <div className="song-select-list">
                  {songs.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      className={`song-select-item ${selectedSongIds.includes(s.id) ? 'selected' : ''}`}
                      onClick={() => toggleSong(s.id)}
                    >
                      {s.title} - {s.artist}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* 메모 */}
            <div className="form-group">
              <label>메모</label>
              <textarea
                value={memo}
                onChange={(e) => setMemo(e.target.value)}
                placeholder="메모를 입력하세요"
                rows={3}
              />
            </div>

            {error && <p className="form-error">{error}</p>}
          </div>

          <div className="modal-footer">
            <button type="button" className="modal-cancel-btn" onClick={onClose}>
              취소
            </button>
            <button type="submit" className="modal-save-btn" disabled={saving}>
              {saving ? '저장 중...' : isEdit ? '수정' : '추가'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default RehearsalModal;
