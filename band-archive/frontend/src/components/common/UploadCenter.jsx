import React, { useContext, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UploadContext } from '../../context/UploadContext';
import { getUploadStatusLabel, isUploadCancellable, isUploadInProgress } from '../../services/uploadPresentation';
import './UploadCenter.css';

const UploadCenter = () => {
  const queue = useContext(UploadContext);
  const { tasks = [], cancel = () => {}, retryTask = async () => {}, clearHistory = () => {} } = queue || {};
  const [open, setOpen] = useState(false);
  const triggerRef = useRef(null);
  const closeRef = useRef(null);
  const dialogRef = useRef(null);
  const navigate = useNavigate();
  const activeCount = tasks.filter((task) => isUploadInProgress(task.status)).length;

  const close = () => { setOpen(false); requestAnimationFrame(() => triggerRef.current?.focus()); };
  useEffect(() => {
    if (!open) return undefined;
    closeRef.current?.focus();
    const keydown = (event) => {
      if (event.key === 'Escape') { event.preventDefault(); close(); return; }
      if (event.key !== 'Tab') return;
      const focusable = [...(dialogRef.current?.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || [])]
        .filter((element) => !element.hasAttribute('hidden'));
      if (focusable.length === 0) { event.preventDefault(); return; }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', keydown);
    return () => document.removeEventListener('keydown', keydown);
  }, [open]);

  const goToTarget = (task) => {
    const base = task.bandSlug ? `/bands/${encodeURIComponent(task.bandSlug)}` : '';
    if (task.target?.memberId) navigate(`${base}/members/${task.target.memberId}`);
    else if (task.target?.songId) navigate(`${base}/songs/${task.target.songId}`);
    else navigate('/');
    close();
  };

  return <>
    <button ref={triggerRef} type="button" className="nav-btn upload-center-trigger" onClick={() => setOpen(true)} aria-haspopup="dialog" aria-expanded={open}>
      ⬆️ 업로드함{activeCount > 0 && <span className="upload-center-count" aria-label={`진행 중 ${activeCount}개`}>{activeCount}</span>}
    </button>
    {open && <div className="upload-center-backdrop" onMouseDown={close}>
      <section ref={dialogRef} className="upload-center-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-center-title" onMouseDown={(event) => event.stopPropagation()}>
        <header><div><h2 id="upload-center-title">영상·음원 업로드함</h2><p>탭 이동 중에도 업로드가 계속됩니다. 웹에서는 새로고침·브라우저 종료·화면 잠금 시 중단될 수 있고, 모바일 앱의 네이티브 업로드는 앱이 다시 열리면 상태를 복구합니다.</p></div><button ref={closeRef} type="button" onClick={close} aria-label="업로드함 닫기">×</button></header>
        {tasks.length === 0 ? <div className="upload-center-empty">진행하거나 최근 완료된 업로드가 없습니다.</div> : <ul className="upload-center-list">
          {tasks.slice().reverse().map((task) => <li key={task.id} className={`upload-center-item ${task.status}`}>
            <div className="upload-center-main"><strong>{task.name}</strong><span>{task.bandName ? `${task.bandName} · ${task.targetLabel}` : task.targetLabel}</span><span>{getUploadStatusLabel(task.status, task.progress)}{task.status === 'uploading' ? ` (${task.progress || 0}%)` : ''}</span>{task.status === 'uploading' && <progress value={task.progress || 0} max="100" aria-label={`${task.name} 업로드 ${task.progress || 0}%`} />}{task.error && <span className="upload-center-error">{task.error}</span>}</div>
            <div className="upload-center-actions">{isUploadCancellable(task.status) && <button type="button" onClick={() => cancel(task.id)}>취소</button>}{['failed', 'processing_failed'].includes(task.status) && !task.retryUnavailable && <button type="button" onClick={() => retryTask(task.id)}>재시도</button>}{task.mediaId && <button type="button" onClick={() => goToTarget(task)}>대상 열기</button>}</div>
          </li>)}
        </ul>}
        {tasks.some((task) => !isUploadInProgress(task.status)) && <button type="button" className="upload-center-clear" onClick={clearHistory}>완료·실패 기록 지우기</button>}
      </section>
    </div>}
  </>;
};

export default UploadCenter;
