import React, { useState, useEffect, useRef } from 'react';
import { fetchAnnouncement, updateAnnouncement } from '../../services/api';
import './AnnouncementToast.css';

const AnnouncementToast = () => {
    const [content, setContent] = useState('');
    const [isEditing, setIsEditing] = useState(false);
    const [editValue, setEditValue] = useState('');
    const [saving, setSaving] = useState(false);
    const inputRef = useRef(null);

    useEffect(() => {
        loadAnnouncement();
    }, []);

    useEffect(() => {
        if (isEditing && inputRef.current) {
            inputRef.current.focus();
            inputRef.current.select();
        }
    }, [isEditing]);

    const loadAnnouncement = async () => {
        try {
            const data = await fetchAnnouncement();
            setContent(data.content || '');
        } catch (error) {
            console.error('Failed to load announcement:', error);
        }
    };

    const handleEdit = () => {
        setEditValue(content);
        setIsEditing(true);
    };

    const handleSave = async () => {
        const trimmed = editValue.trim();
        if (!trimmed) return;

        setSaving(true);
        try {
            const data = await updateAnnouncement(trimmed);
            setContent(data.content);
            setIsEditing(false);
        } catch (error) {
            console.error('Failed to update announcement:', error);
        } finally {
            setSaving(false);
        }
    };

    const handleCancel = () => {
        setIsEditing(false);
        setEditValue('');
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            handleSave();
        } else if (e.key === 'Escape') {
            handleCancel();
        }
    };

    return (
        <div className="announcement-toast">
            <span className="announcement-icon">📢</span>

            {isEditing ? (
                <div className="announcement-edit">
                    <input
                        ref={inputRef}
                        type="text"
                        className="announcement-input"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="공지사항을 입력하세요..."
                        disabled={saving}
                    />
                    <div className="announcement-edit-actions">
                        <button
                            className="announcement-save-btn"
                            onClick={handleSave}
                            disabled={saving || !editValue.trim()}
                        >
                            {saving ? '저장 중...' : '저장'}
                        </button>
                        <button
                            className="announcement-cancel-btn"
                            onClick={handleCancel}
                            disabled={saving}
                        >
                            취소
                        </button>
                    </div>
                </div>
            ) : (
                <div className="announcement-display" onClick={handleEdit}>
                    <span className="announcement-text">
                        {content || '공지사항을 입력하세요...'}
                    </span>
                    <button
                        className="announcement-edit-btn"
                        onClick={(e) => {
                            e.stopPropagation();
                            handleEdit();
                        }}
                        title="공지 수정"
                    >
                        ✏️
                    </button>
                </div>
            )}
        </div>
    );
};

export default AnnouncementToast;
