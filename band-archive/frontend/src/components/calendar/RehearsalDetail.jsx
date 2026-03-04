import React, { useState, useEffect } from 'react';
import { deleteRehearsal, fetchRehearsalMedia, uploadRehearsalMedia } from '../../services/rehearsalApi';
import MediaPlayer from '../common/MediaPlayer';
import CommentSection from '../common/CommentSection';
import './RehearsalDetail.css';

const RehearsalDetail = ({ date, rehearsals, onEdit, onDelete, onAdd }) => {
  const [mediaMap, setMediaMap] = useState({});
  const [expandedMediaIds, setExpandedMediaIds] = useState(new Set());
  const [uploadingFor, setUploadingFor] = useState(null);
  const [pendingFiles, setPendingFiles] = useState([]);
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

  const handleFilesSelected = (e) => {
    const files = Array.from(e.target.files);
    if (files.length === 0) return;
    setPendingFiles(prev => [
      ...prev,
      ...files.map(f => ({
        file: f,
        songId: '',
        progress: 0,
        status: 'pending',
      })),
    ]);
  };

  const updatePendingFile = (index, key, value) => {
    setPendingFiles(prev => prev.map((item, i) =>
      i === index ? { ...item, [key]: value } : item
    ));
  };

  const removePendingFile = (index) => {
    setPendingFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleBatchUpload = async (rehearsalId) => {
    setUploading(true);
    let hasError = false;
    try {
      for (let i = 0; i < pendingFiles.length; i++) {
        const item = pendingFiles[i];
        if (item.status === 'done' || !item.songId) continue;

        setPendingFiles(prev => prev.map((f, idx) =>
          idx === i ? { ...f, status: 'uploading', progress: 0 } : f
        ));

        try {
          await uploadRehearsalMedia(rehearsalId, item.songId, item.file, (p) => {
            setPendingFiles(prev => prev.map((f, idx) =>
              idx === i ? { ...f, progress: p } : f
            ));
          });
          setPendingFiles(prev => prev.map((f, idx) =>
            idx === i ? { ...f, status: 'done', progress: 100 } : f
          ));
        } catch (err) {
          hasError = true;
          setPendingFiles(prev => prev.map((f, idx) =>
            idx === i ? { ...f, status: 'error' } : f
          ));
        }
      }

      const media = await fetchRehearsalMedia(rehearsalId);
      setMediaMap(prev => ({ ...prev, [rehearsalId]: media }));

      if (!hasError) {
        setUploadingFor(null);
        setPendingFiles([]);
      }
    } finally {
      setUploading(false);
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
                            <CommentSection targetType="media" targetId={m.id} />
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
                    <input
                      type="file"
                      multiple
                      accept="audio/*,video/*,image/*,.pdf,.mp3,.wav,.m4a,.mp4,.mov"
                      className="rd-file-input"
                      onChange={handleFilesSelected}
                    />

                    {pendingFiles.length > 0 && (
                      <div className="rd-upload-queue">
                        {pendingFiles.map((item, i) => (
                          <div key={i} className={`rd-upload-queue-item ${item.status}`}>
                            <span className="rd-queue-filename">{item.file.name}</span>
                            <select
                              value={item.songId}
                              onChange={(e) => updatePendingFile(i, 'songId', e.target.value)}
                              disabled={item.status !== 'pending'}
                              className="rd-song-select"
                            >
                              <option value="">곡 선택</option>
                              {r.songs.map((s) => (
                                <option key={s.id} value={s.id}>{s.title} - {s.artist}</option>
                              ))}
                            </select>
                            {item.status === 'pending' && (
                              <button className="rd-queue-remove" onClick={() => removePendingFile(i)}>✕</button>
                            )}
                            {item.status === 'uploading' && (
                              <progress value={item.progress} max="100" className="rd-queue-progress" />
                            )}
                            {item.status === 'done' && <span className="rd-queue-status done">✓</span>}
                            {item.status === 'error' && <span className="rd-queue-status error">✕</span>}
                          </div>
                        ))}
                      </div>
                    )}

                    <div className="rd-upload-actions">
                      <button
                        className="rd-upload-btn"
                        onClick={() => handleBatchUpload(r.id)}
                        disabled={pendingFiles.length === 0 || pendingFiles.some(f => !f.songId && f.status === 'pending') || uploading}
                      >
                        {uploading ? '업로드 중...' : `업로드 (${pendingFiles.filter(f => f.songId).length}개)`}
                      </button>
                      <button
                        className="rd-upload-cancel"
                        onClick={() => { setUploadingFor(null); setPendingFiles([]); }}
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
