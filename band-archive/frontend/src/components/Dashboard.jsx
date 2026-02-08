import React, { useState, useEffect } from 'react';
import { getRecentUploads } from '../services/api';
import './Dashboard.css';

const Dashboard = ({ onSelectSong }) => {
  const [recentUploads, setRecentUploads] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadRecentUploads();
  }, []);

  const loadRecentUploads = async () => {
    try {
      const uploads = await getRecentUploads(5);
      setRecentUploads(uploads);
    } catch (error) {
      console.error('Failed to load recent uploads:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <h2>🎸 들뜬 대시보드</h2>
        <p className="dashboard-subtitle">밴드 활동을 한눈에 확인하세요</p>
      </div>

      <div className="dashboard-grid">
        {/* Recent Uploads Section */}
        <div className="dashboard-card">
          <h3>📁 최근 업로드</h3>
          {loading ? (
            <div className="loading-small">로딩 중...</div>
          ) : recentUploads.length > 0 ? (
            <ul className="recent-list">
              {recentUploads.map((song) => (
                <li 
                  key={song.id} 
                  className="recent-item"
                  onClick={() => onSelectSong(song)}
                >
                  <div className="recent-info">
                    <span className="recent-title">{song.title}</span>
                    <span className="recent-artist">{song.artist}</span>
                  </div>
                  <span className="recent-date">
                    {new Date(song.updated_at).toLocaleDateString('ko-KR')}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty-card">아직 업로드된 파일이 없습니다</div>
          )}
        </div>

        {/* Quick Actions */}
        <div className="dashboard-card">
          <h3>⚡ 빠른 작업</h3>
          <div className="quick-actions">
            <button className="action-btn" onClick={() => window.location.reload()}>
              🔄 새로고침
            </button>
            <button className="action-btn">
              📊 통계 보기
            </button>
            <button className="action-btn">
              🎵 전체 곡 보기
            </button>
          </div>
        </div>

        {/* Practice Tips */}
        <div className="dashboard-card tips-card">
          <h3>💡 연습 팁</h3>
          <div className="tips-content">
            <p>✨ 정기적으로 연습 영상을 업로드하여 발전 과정을 기록하세요</p>
            <p>🎯 각 곡의 메모 기능을 활용하여 연습 포인트를 정리하세요</p>
            <p>🎸 어려운 부분은 반복 연습하고 기록을 남기세요</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
