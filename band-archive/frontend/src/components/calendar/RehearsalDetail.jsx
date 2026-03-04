import React, { useState, useEffect } from 'react';
import { deleteRehearsal, fetchRehearsalMedia, uploadRehearsalMedia } from '../../services/rehearsalApi';
import MediaPlayer from '../common/MediaPlayer';
import './RehearsalDetail.css';

const RehearsalDetail = ({ date, rehearsals, onEdit, onDelete, onAdd }) => {
  const [mediaMap, setMediaMap] = useState({});
  const [expandedMediaIds, setExpandedMediaIds] = useState(new Set());
  const [uploadingFor, setUploadingFor] = useState(null);
  const [uploadSongId, setUploadSongId] = useState('');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);

  const dateStr = date.toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  });

  // Load media for rehearsals that have media_count > 0
  useEffect(() => {
    const loadMedia = async () => {
      const newMap = {};
      for (const r of rehearsals) {
        if (r.media_count > 0) {
          try {
            newMap[r.id] = await fetchRehearsalMedia(r.id);
          } catch (e) {
            newMap[r.id] = [];
          }
        }
      }
      setMediaMap(newMap);
    };
    if (rehearsals.length > 0) loadMedia();
  }, [rehearsals]);

  const handleDelete = async (id) => {
    if (!window.confirm('이 일정을 삭제하시겠습니까?')) return;
    try {
      await deleteRehearsal(id);
      onDelete();
    } catch (error) {
      console.error('Failed to delete rehearsal:', error);
    }
  };

  const handleUpload = async (rehearsalId) => {
    const fileInput = document.getElementById(`rehearsal-file-${rehearsalId}`);
    const file = fileInput?.files[0];
    if (!file || !uploadSongId) return;

    setUploading(true);
    setUploadProgress(0);
    try {
      await uploadRehearsalMedia(rehearsalId, uploadSongId, file, (p) => setUploadProgress(p));
      // Refresh media for this rehearsal
      const media = await fetchRehearsalMedia(rehearsalId);
      setMediaMap(prev => ({ ...prev, [rehearsalId]: media }));
      setUploadingFor(null);
      setUploadSongId('');
      fileInput.value = '';
    } catch (err) {
      alert('업로드 실패: ' + err.message);
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const getMediaIcon = (type) => {
    switch (type) {
      case 'video': return '🎬';
      case 'audio': return '🎵';
      case 'image': return '🖼️';
      default: return '📄';
    }
  };

  const toggleMediaExpand = (mediaId) => {
    setExpandedMediaIds(prev => {
      const next = new Set(prev);
      if (next.has(mediaId)) {
        next.delete(mediaId);
      } else {
        next.add(mediaId);
      }
      return next;
    });
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

              {r.location && (
                <div className="detail-location">
                  📍{' '}
                  {r.latitude && r.longitude ? (
                    <a
                      href={`https://map.naver.com/p/search/${encodeURIComponent(r.location)}?c=${r.longitude},${r.latitude},15,0,0,0,dh`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="detail-location-link"
                    >
                      {r.location}
                    </a>
                  ) : (
                    r.location
                  )}
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

              {/* Linked media list */}
              {mediaMap[r.id] && mediaMap[r.id].length > 0 && (
                <div className="rd-media-list">
                  <div className="rd-media-title">연결된 미디어</div>
                  {mediaMap[r.id].map((m) => {
                    const isExpanded = expandedMediaIds.has(m.id);
                    return (
                      <div key={m.id} className={`rd-media-item ${isExpanded ? 'expanded' : ''}`}>
                        <div className="rd-media-item-header" onClick={() => toggleMediaExpand(m.id)}>
                          <span className="rd-media-icon">{getMediaIcon(m.file_type)}</span>
                          <div className="rd-media-info">
                            <span className="rd-media-name">{m.filename}</span>
                            <span className="rd-media-song">{m.song_title} - {m.song_artist}</span>
                          </div>
                          <span className="expand-indicator">{isExpanded ? '▲' : '▼'}</span>
                        </div>
                        {isExpanded && (
                          <div className="rd-media-item-body">
                            <MediaPlayer file={{
                              id: m.id,
                              name: m.filename,
                              url: m.url,
                              type: m.file_type,
                            }} />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Upload from rehearsal */}
              {r.songs && r.songs.length > 0 && (
                uploadingFor === r.id ? (
                  <div className="rd-upload-form">
                    <select
                      value={uploadSongId}
                      onChange={(e) => setUploadSongId(e.target.value)}
                      className="rd-song-select"
                    >
                      <option value="">곡 선택</option>
                      {r.songs.map((s) => (
                        <option key={s.id} value={s.id}>{s.title} - {s.artist}</option>
                      ))}
                    </select>
                    <input
                      id={`rehearsal-file-${r.id}`}
                      type="file"
                      accept="audio/*,video/*,image/*,.pdf,.mp3,.wav,.m4a,.mp4,.mov"
                      className="rd-file-input"
                    />
                    <div className="rd-upload-actions">
                      <button
                        className="rd-upload-btn"
                        onClick={() => handleUpload(r.id)}
                        disabled={!uploadSongId || uploading}
                      >
                        {uploading ? `${Math.round(uploadProgress)}%` : '업로드'}
                      </button>
                      <button
                        className="rd-upload-cancel"
                        onClick={() => { setUploadingFor(null); setUploadSongId(''); }}
                        disabled={uploading}
                      >
                        취소
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    className="rd-add-media-btn"
                    onClick={() => setUploadingFor(r.id)}
                  >
                    + 미디어 추가
                  </button>
                )
              )}

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
