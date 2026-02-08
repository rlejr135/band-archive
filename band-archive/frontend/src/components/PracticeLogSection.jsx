import React, { useState, useEffect, useCallback } from 'react';
import {
  fetchPracticeLogs,
  createPracticeLog,
  updatePracticeLog,
  deletePracticeLog,
  uploadRecording,
  API_URL,
} from '../services/api';
import './PracticeLogSection.css';

const PracticeLogSection = ({ songId }) => {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingLog, setEditingLog] = useState(null);
  const [formData, setFormData] = useState({ content: '', feedback: '' });

  const loadLogs = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchPracticeLogs(songId);
      setLogs(data);
    } catch (error) {
      console.error('Failed to load practice logs:', error);
    } finally {
      setLoading(false);
    }
  }, [songId]);

  useEffect(() => {
    if (songId) {
      loadLogs();
    }
  }, [songId, loadLogs]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editingLog) {
        const updated = await updatePracticeLog(editingLog.id, formData);
        setLogs(logs.map(l => l.id === editingLog.id ? updated : l));
      } else {
        const newLog = await createPracticeLog(songId, formData);
        setLogs([newLog, ...logs]);
      }
      resetForm();
    } catch (error) {
      console.error('Failed to save practice log:', error);
    }
  };

  const handleEdit = (log) => {
    setEditingLog(log);
    setFormData({ content: log.content || '', feedback: log.feedback || '' });
    setShowForm(true);
  };

  const handleDelete = async (id) => {
    if (!window.confirm('정말 이 연습 일지를 삭제하시겠습니까?')) {
      return;
    }
    
    try {
      await deletePracticeLog(id);
      setLogs(logs.filter(l => l.id !== id));
    } catch (error) {
      console.error('Failed to delete practice log:', error);
      alert('삭제에 실패했습니다. 다시 시도해주세요.');
    }
  };

  const handleRecordingUpload = async (logId, e) => {
    const file = e.target.files[0];
    if (!file) return;

    try {
      const updated = await uploadRecording(logId, file);
      setLogs(logs.map(l => l.id === logId ? updated : l));
    } catch (error) {
      console.error('Failed to upload recording:', error);
    }
  };

  const resetForm = () => {
    setShowForm(false);
    setEditingLog(null);
    setFormData({ content: '', feedback: '' });
  };

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  return (
    <div className="practice-log-section">
      <div className="practice-log-header">
        <h4>📝 연습 일지</h4>
        {!showForm && (
          <button className="add-log-btn" onClick={() => setShowForm(true)}>
            + 새 일지
          </button>
        )}
      </div>

      {/* Form */}
      {showForm && (
        <form className="practice-log-form" onSubmit={handleSubmit}>
          <div className="form-group">
            <label>연습 내용</label>
            <textarea
              name="content"
              value={formData.content}
              onChange={handleChange}
              rows="3"
              placeholder="오늘 연습한 내용을 기록하세요"
            />
          </div>
          <div className="form-group">
            <label>피드백</label>
            <textarea
              name="feedback"
              value={formData.feedback}
              onChange={handleChange}
              rows="2"
              placeholder="개선할 점이나 피드백을 남기세요"
            />
          </div>
          <div className="practice-log-form-actions">
            <button type="submit" className="save-btn">
              {editingLog ? '수정' : '저장'}
            </button>
            <button type="button" className="cancel-btn" onClick={resetForm}>
              취소
            </button>
          </div>
        </form>
      )}

      {/* List */}
      {loading ? (
        <div className="loading-small">로딩 중...</div>
      ) : logs.length > 0 ? (
        <ul className="practice-log-list">
          {logs.map((log) => (
            <li key={log.id} className="practice-log-item">
              <div className="practice-log-content">
                <div className="practice-log-date">
                  {new Date(log.date).toLocaleDateString('ko-KR')}
                </div>
                {log.content && <p className="log-text">{log.content}</p>}
                {log.feedback && (
                  <p className="log-feedback">💬 {log.feedback}</p>
                )}
                {log.recording && (
                  <div className="log-recording">
                    🎵 <a href={`${API_URL}/uploads/${log.recording}`} target="_blank" rel="noreferrer">
                      {log.recording}
                    </a>
                  </div>
                )}
              </div>
              <div className="practice-log-actions">
                <label className="upload-recording-btn">
                  🎤
                  <input
                    type="file"
                    accept="audio/*,video/*"
                    onChange={(e) => handleRecordingUpload(log.id, e)}
                    hidden
                  />
                </label>
                <button className="log-edit-btn" onClick={() => handleEdit(log)}>✏️</button>
                <button className="log-delete-btn" onClick={() => handleDelete(log.id)}>🗑️</button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="empty-logs">아직 연습 일지가 없습니다. 새 일지를 추가해보세요!</div>
      )}
    </div>
  );
};

export default PracticeLogSection;
