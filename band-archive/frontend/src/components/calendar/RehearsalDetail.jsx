import React from 'react';
import { deleteRehearsal } from '../../services/rehearsalApi';
import './RehearsalDetail.css';

const RehearsalDetail = ({ date, rehearsals, onEdit, onDelete, onAdd }) => {
  const dateStr = date.toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  });

  const handleDelete = async (id) => {
    if (!window.confirm('이 일정을 삭제하시겠습니까?')) return;
    try {
      await deleteRehearsal(id);
      onDelete();
    } catch (error) {
      console.error('Failed to delete rehearsal:', error);
    }
  };

  return (
    <div className="rehearsal-detail">
      <div className="detail-date-header">
        <span className="detail-date">{dateStr}</span>
        <span className="detail-count">
          {rehearsals.length > 0 ? `${rehearsals.length}건` : ''}
        </span>
      </div>

      {rehearsals.length === 0 ? (
        <div className="detail-empty">
          <p>등록된 일정이 없습니다</p>
          <button className="detail-add-btn" onClick={onAdd}>
            + 이 날짜에 일정 추가
          </button>
        </div>
      ) : (
        <ul className="detail-list">
          {rehearsals.map((r) => (
            <li key={r.id} className="detail-item">
              <div className="detail-item-header">
                <span
                  className="detail-color-bar"
                  style={{ backgroundColor: r.color || '#ffd32a' }}
                />
                <span className="detail-title">{r.title}</span>
                {r.time && <span className="detail-time">{r.time}</span>}
              </div>

              {r.start_date && r.end_date && (
                <div className="detail-period">
                  {r.start_date} ~ {r.end_date}
                </div>
              )}

              {r.songs && r.songs.length > 0 && (
                <div className="detail-songs">
                  {r.songs.map((s) => (
                    <span key={s.id} className="detail-song-tag">
                      {s.title} - {s.artist}
                    </span>
                  ))}
                </div>
              )}

              {r.memo && <p className="detail-memo">{r.memo}</p>}

              <div className="detail-actions">
                <button className="detail-edit-btn" onClick={() => onEdit(r)}>
                  수정
                </button>
                <button className="detail-delete-btn" onClick={() => handleDelete(r.id)}>
                  삭제
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default RehearsalDetail;
