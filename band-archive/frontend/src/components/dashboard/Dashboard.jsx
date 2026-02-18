import React, { useState, useEffect } from 'react';
import { fetchDashboardStats, fetchSongs } from '../../services/api';
import RehearsalCalendar from '../calendar/RehearsalCalendar';
import './Dashboard.css';

const Dashboard = ({ onSelectSong, onViewSongs }) => {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [expandedStatus, setExpandedStatus] = useState(null);
  const [statusSongs, setStatusSongs] = useState([]);
  const [songsLoading, setSongsLoading] = useState(false);

  useEffect(() => {
    loadStats();
  }, []);

  const loadStats = async () => {
    try {
      const data = await fetchDashboardStats();
      setStats(data);
    } catch (error) {
      console.error('Failed to load dashboard stats:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleStatusClick = async (status) => {
    if (expandedStatus === status) {
      setExpandedStatus(null);
      setStatusSongs([]);
      return;
    }
    setExpandedStatus(status);
    setSongsLoading(true);
    try {
      const allSongs = await fetchSongs();
      setStatusSongs(allSongs.filter((s) => s.status === status));
    } catch (error) {
      console.error('Failed to load songs:', error);
      setStatusSongs([]);
    } finally {
      setSongsLoading(false);
    }
  };

  if (loading) {
    return <div className="dashboard"><div className="loading-small">로딩 중...</div></div>;
  }

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <h2>🎸 들뜬 대시보드</h2>
        <p className="dashboard-subtitle">밴드 활동을 한눈에 확인하세요</p>
      </div>

      <div className="dashboard-grid">
        {/* Stats Overview */}
        <div className="dashboard-card">
          <h3>📈 전체 통계</h3>
          <div className="stats-grid">
            <div className="stat-item">
              <span className="stat-value">{stats?.total_songs ?? 0}</span>
              <span className="stat-label">전체 곡</span>
            </div>
            <div className="stat-item">
              <span className="stat-value">{stats?.total_practice_logs ?? 0}</span>
              <span className="stat-label">연습 일지</span>
            </div>
          </div>
        </div>

        {/* Status Counts */}
        <div className="dashboard-card">
          <h3>🎵 곡 상태</h3>
          <div className="status-list">
            {[
              { key: 'Practice', label: '연습중', className: 'practice' },
              { key: 'Completed', label: '완료', className: 'completed' },
              { key: 'OnHold', label: '보류', className: 'onhold' },
            ].map(({ key, label, className }) => (
              <React.Fragment key={key}>
                <div
                  className={`status-item clickable ${expandedStatus === key ? 'active' : ''}`}
                  onClick={() => handleStatusClick(key)}
                >
                  <span className={`status-badge ${className}`}>{label}</span>
                  <span className="status-count">
                    {stats?.status_counts?.[key] ?? 0}곡
                    <span className={`status-arrow ${expandedStatus === key ? 'open' : ''}`}>▾</span>
                  </span>
                </div>
                {expandedStatus === key && (
                  <div className="status-songs">
                    {songsLoading ? (
                      <div className="status-songs-loading">로딩 중...</div>
                    ) : statusSongs.length > 0 ? (
                      statusSongs.map((song) => (
                        <div
                          key={song.id}
                          className="status-song-item"
                          onClick={() => onSelectSong(song)}
                        >
                          <span className="status-song-title">{song.title}</span>
                          <span className="status-song-artist">{song.artist}</span>
                        </div>
                      ))
                    ) : (
                      <div className="status-songs-empty">해당 곡이 없습니다</div>
                    )}
                  </div>
                )}
              </React.Fragment>
            ))}
          </div>
        </div>

        {/* Rehearsal Calendar */}
        <RehearsalCalendar />

        {/* Recent Practice Logs */}
        <div className="dashboard-card">
          <h3>📝 최근 연습 일지</h3>
          {stats?.recent_practice_logs?.length > 0 ? (
            <ul className="recent-list">
              {stats.recent_practice_logs.map((log) => (
                <li
                  key={log.id}
                  className="recent-item"
                  onClick={() => onSelectSong({ id: log.song_id })}
                >
                  <div className="recent-info">
                    <span className="recent-title">{log.song_title}</span>
                    <span className="recent-artist">{log.content || '내용 없음'}</span>
                  </div>
                  <span className="recent-date">
                    {new Date(log.date).toLocaleDateString('ko-KR')}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty-card">아직 연습 일지가 없습니다</div>
          )}
        </div>

        {/* Quick Actions */}
        <div className="dashboard-card">
          <h3>⚡ 빠른 작업</h3>
          <div className="quick-actions">
            <button className="action-btn" onClick={() => loadStats()}>
              🔄 새로고침
            </button>
            <button className="action-btn" onClick={onViewSongs}>
              🎵 전체 곡 보기
            </button>
          </div>
        </div>

        {/* Practice Tips */}
        <div className="dashboard-card tips-card">
          <h3>💡 연습 팁</h3>
          <div className="tips-content">
            <p>✨ 정기적으로 연습 영상을 업로드하여 발전 과정을 기록하세요</p>
            <p>🎯 각 곡의 연습 일지 기능을 활용하여 연습 포인트를 정리하세요</p>
            <p>🎸 어려운 부분은 반복 연습하고 기록을 남기세요</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
