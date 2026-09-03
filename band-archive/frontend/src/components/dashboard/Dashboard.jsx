import React, { useState, useEffect } from 'react';
import { fetchDashboardStats } from '../../services/api';
import { useSongs } from '../../context/SongContext';
import { fetchFeaturedImage } from '../../services/galleryApi';
import RehearsalCalendar from '../calendar/RehearsalCalendar';
import './Dashboard.css';

const Dashboard = ({ onSelectSong, onViewSongs }) => {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [expandedStatus, setExpandedStatus] = useState(null);
  const [featuredImage, setFeaturedImage] = useState(null);
  const { songs } = useSongs();

  useEffect(() => {
    loadStats();
    fetchFeaturedImage().then(setFeaturedImage).catch(console.error);
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

  const handleStatusClick = (status) => {
    if (expandedStatus === status) {
      setExpandedStatus(null);
      return;
    }
    setExpandedStatus(status);
  };

  const statusSongs = expandedStatus ? songs.filter((song) => song.status === expandedStatus) : [];

  if (loading) {
    return <div className="dashboard"><div className="loading-small">로딩 중...</div></div>;
  }

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <h2>🎸 들뜬 대시보드</h2>
        <p className="dashboard-subtitle">밴드 활동을 한눈에 확인하세요</p>
      </div>

      {featuredImage && (
        <div className="dashboard-card featured-image-card">
          <h3>📷 대표 사진</h3>
          <img src={featuredImage.url} alt="대표 이미지" className="featured-image" />
        </div>
      )}

      <div className="dashboard-grid">
        {/* Stats Overview */}
        <div className="dashboard-card">
          <h3>📈 전체 통계</h3>
          <div className="stats-grid">
            <div className="stat-item">
              <span className="stat-value">{stats?.total_songs ?? 0}</span>
              <span className="stat-label">전체 곡</span>
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
                    {statusSongs.length > 0 ? (
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
            <p>🎸 어려운 부분은 반복 연습하고 기록을 남기세요</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
