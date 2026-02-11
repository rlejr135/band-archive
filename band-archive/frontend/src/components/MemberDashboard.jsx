import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMembers, createMember } from '../services/memberApi';
import './MemberDashboard.css';

const MemberDashboard = () => {
    const [members, setMembers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [showForm, setShowForm] = useState(false);
    const [newMember, setNewMember] = useState({ name: '', instrument: '' });

    const navigate = useNavigate();

    useEffect(() => {
        loadMembers();
    }, []);

    const loadMembers = async () => {
        try {
            const data = await fetchMembers();
            setMembers(data);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleCreate = async (e) => {
        e.preventDefault();
        try {
            await createMember(newMember);
            setShowForm(false);
            setNewMember({ name: '', instrument: '' });
            loadMembers();
        } catch (err) {
            alert(err.message);
        }
    };

    if (loading) return <div className="loading">멤버 로딩 중...</div>;
    if (error) return <div className="error">{error}</div>;

    return (
        <div className="member-dashboard fade-in">
            <div className="dashboard-header">
                <h2>전체 멤버</h2>
                <button className="primary-btn" onClick={() => setShowForm(!showForm)}>
                    {showForm ? '취소' : '멤버 추가'}
                </button>
            </div>

            {showForm && (
                <form className="member-form" onSubmit={handleCreate}>
                    <input
                        type="text"
                        placeholder="이름"
                        value={newMember.name}
                        onChange={(e) => setNewMember({ ...newMember, name: e.target.value })}
                        required
                    />
                    <input
                        type="text"
                        placeholder="악기 (예: Drums)"
                        value={newMember.instrument}
                        onChange={(e) => setNewMember({ ...newMember, instrument: e.target.value })}
                        required
                    />
                    <button type="submit" className="primary-btn">저장</button>
                </form>
            )}

            <div className="member-grid">
                {members.map(member => (
                    <div key={member.id} className="member-card" onClick={() => navigate(`/members/${member.id}`)}>
                        <div className="member-avatar">{member.name[0]}</div>
                        <h3>{member.name}</h3>
                        <p className="instrument">{member.instrument}</p>
                    </div>
                ))}
                
                {members.length === 0 && !loading && (
                    <div className="empty-members">
                        <p>등록된 멤버가 없습니다.</p>
                    </div>
                )}
            </div>
        </div>
    );
};

export default MemberDashboard;
