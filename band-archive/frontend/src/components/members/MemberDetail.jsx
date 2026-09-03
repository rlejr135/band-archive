import React, { useCallback, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getMember, fetchMemberLogs, deletePersonalLog, deleteMember } from '../../services/memberApi';
import FileUpload from '../common/FileUpload';
import MediaPlayer from '../common/MediaPlayer';
import CommentSection from '../common/CommentSection';
import useAsyncData from '../../hooks/useAsyncData';
import { getMediaIcon } from '../../services/mediaPresentation';
import './MemberDetail.css';

const MemberDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();

  const { data: member, loading: memberLoading, error: memberError } = useAsyncData(
    () => getMember(id), [id]
  );
  const { data: logs, setData: setLogs, loading: logsLoading, error: logsError } = useAsyncData(
    () => fetchMemberLogs(id), [id]
  );

  const [playingLog, setPlayingLog] = useState(null);

  const loading = memberLoading || logsLoading;
  const error = memberError || logsError;

  const handleLogUpdate = useCallback((updated) => {
    if (!updated?.id) return;
    setLogs((previous) => {
      const current = previous || [];
      const exists = current.some((log) => log.id === updated.id);
      return exists
        ? current.map((log) => log.id === updated.id ? { ...log, ...updated } : log)
        : [updated, ...current];
    });
    setPlayingLog((previous) => previous?.id === updated.id ? { ...previous, ...updated } : previous);
  }, [setLogs]);

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
  if (error) return <div className="error-state">오류: {error}</div>;
  if (!member) return <div className="error-state">멤버를 찾을 수 없습니다.</div>;

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
          memberId={id}
          onMediaComplete={handleLogUpdate}
          accept="audio/*,video/*,.mp3,.wav,.m4a,.mp4,.mov,.avi"
          multiple={false}
        />
      </div>

      <div className="logs-list">
        <h3>연습 기록 ({logs ? logs.length : 0})</h3>

        {playingLog && (
          <div className="inline-player-wrapper">
            <button className="close-player-btn" onClick={() => setPlayingLog(null)}>&times;</button>
            <MediaPlayer
              file={{
                id: playingLog.id,
                url: playingLog.url,
                name: playingLog.title,
                type: playingLog.file_type,
                audio_url: playingLog.audio_url,
                audio_filename: playingLog.audio_filename,
                transcoding_status: playingLog.transcoding_status,
                processing_error: playingLog.processing_error,
                transcoding_error: playingLog.transcoding_error || playingLog.processing_error || playingLog.error,
              }}
              mediaKind="personal_log"
              onMediaUpdate={handleLogUpdate}
            />
            <CommentSection targetType="personal-logs" targetId={playingLog.id} />
          </div>
        )}

        {!logs || logs.length === 0 ? (
          <p className="empty-state-box">기록이 없습니다.</p>
        ) : (
          <div className="logs-grid">
            {logs.map(log => (
              <div key={log.id} className="log-card">
                <div className="log-icon-wrapper">
                  <span className="log-icon">
                    {getMediaIcon(log)}
                  </span>
                </div>
                <div className="log-info">
                  <div className="log-title" title={log.title}>{log.title}</div>
                  <div className="log-meta">
                    {log.file_size && (
                      <span>{(log.file_size / (1024 * 1024)).toFixed(2)} MB</span>
                    )}
                    {/* If file_size is missing, show date as fallback */}
                    {!log.file_size && (
                      <span>{new Date(log.created_at).toLocaleDateString()}</span>
                    )}
                  </div>
                </div>
                <div className="log-actions">
                  <button className="play-btn" onClick={() => setPlayingLog(log)}>
                    ▶ 재생
                  </button>
                  <button className="delete-action-btn" onClick={() => handleDeleteLog(log.id)} title="삭제">
                    🗑️
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default MemberDetail;
