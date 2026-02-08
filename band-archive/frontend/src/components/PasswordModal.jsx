import React, { useState } from 'react';
import './PasswordModal.css';

const PasswordModal = ({ isOpen, onClose, onConfirm, title = "비밀번호 확인" }) => {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  if (!isOpen) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    
    // 초기 비밀번호 확인
    if (password === 'admin') {
      onConfirm();
      handleClose();
    } else {
      setError('비밀번호가 올바르지 않습니다.');
      setPassword('');
    }
  };

  const handleClose = () => {
    setPassword('');
    setError('');
    onClose();
  };

  const handleBackdropClick = (e) => {
    if (e.target === e.currentTarget) {
      handleClose();
    }
  };

  return (
    <div className="modal-backdrop" onClick={handleBackdropClick}>
      <div className="modal-content">
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="modal-close-btn" onClick={handleClose}>✕</button>
        </div>
        
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <p className="modal-message">삭제하려면 비밀번호를 입력하세요</p>
            <input
              type="password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setError('');
              }}
              placeholder="비밀번호 입력"
              className="password-input"
              autoFocus
            />
            {error && <p className="error-message">{error}</p>}
          </div>
          
          <div className="modal-footer">
            <button type="button" onClick={handleClose} className="modal-cancel-btn">
              취소
            </button>
            <button type="submit" className="modal-confirm-btn">
              확인
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default PasswordModal;
