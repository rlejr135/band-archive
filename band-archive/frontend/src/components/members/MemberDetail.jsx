import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getMember, fetchMemberLogs, uploadPersonalLog, deletePersonalLog, deleteMember } from '../../services/memberApi';
import FileUpload from '../common/FileUpload';
import MediaPlayer from '../common/MediaPlayer';
import { API_URL } from '../../services/api';
import './MemberDetail.css';

const MemberDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [member, setMember] = useState(null);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadData();
  }, [id]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [memberData, logsData] = await Promise.all([
        getMember(id),
        fetchMemberLogs(id)
      ]);
      setMember(memberData);
      setLogs(logsData);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleUpload = async (file, onProgress) => {
    try {
      // Use filename as title by default
      const title = file.name.split('.')[0];
      await uploadPersonalLog(id, file, title, onProgress);
      loadData(); // Reload logs
    } catch (err) {
      alert('업로드 실패: ' + err.message);
    }
  };

  const handleDeleteLog = async (logId) => {
    if (!window.confirm('정말 삭제하시겠습니까?')) return;
    try {
      await deletePersonalLog(logId);
      setLogs(logs.filter(log => log.id !== logId));
    } catch (err) {
      alert('삭제 실패: ' + err.message);
    }
  };
  
  const handleDeleteMember = async () => {
      if (!window.confirm('멤버와 모든 연습 기록이 삭제됩니다. 계속하시겠습니까?')) return;
      try {
          await deleteMember(id);
          navigate('/members');
      } catch (err) {
          alert('삭제 실패: ' + err.message);
      }
  };

  if (loading) return <div className="loading">로딩 중...</div>;
  if (error) return <div className="error">오류: {error}</div>;
  if (!member) return <div className="error">멤버를 찾을 수 없습니다.</div>;

  return (
    <div className="member-detail fade-in">
      <div className="member-header-section">
        <button className="back-btn" onClick={() => navigate('/members')}>← 목록</button>
        <div className="member-profile">
            <div className="member-avatar-large">{member.name[0]}</div>
            <div className="member-info">
                <h2>{member.name}</h2>
                <span className="instrument-badge">{member.instrument}</span>
            </div>
            <button className="delete-btn-text" onClick={handleDeleteMember}>멤버 삭제</button>
        </div>
      </div>

      <div className="personal-log-upload">
        <h3>개인 연습 기록 업로드</h3>
        <FileUpload 
            onUpload={handleUpload} 
            accept=".mp3,.wav,.m4a,.mp4,.mov,.avi" 
            multiple={false} 
        />
      </div>

      <div className="logs-list">
        <h3>연습 기록 ({logs.length})</h3>
        {logs.length === 0 ? (
            <p className="empty-logs">기록이 없습니다.</p>
        ) : (
            <div className="logs-grid">
                {logs.map(log => (
                    <div key={log.id} className="log-card">
                        <div className="log-header">
                            <h4>{log.title}</h4>
                            <span className="log-date">{new Date(log.created_at).toLocaleDateString()}</span>
                        </div>
                        <MediaPlayer 
                            url={`${API_URL}/uploads/${log.filename}`} 
                            type={log.file_type} 
                        />
                        <button className="delete-log-btn" onClick={() => handleDeleteLog(log.id)}>삭제</button>
                    </div>
                ))}
            </div>
        )}
      </div>
    </div>
  );
};

export default MemberDetail;
