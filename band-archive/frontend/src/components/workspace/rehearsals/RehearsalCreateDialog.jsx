import { useEffect, useId, useLayoutEffect, useRef } from 'react';

const focusable = 'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]';

export const RehearsalCreateDialog = ({ children, saving, onClose, returnFocusRef, fallbackFocusRef }) => {
  const dialog = useRef(null);
  const title = useRef(null);
  const titleId = useId();
  const latest = useRef({ saving, onClose });
  useLayoutEffect(() => { latest.current = { saving, onClose }; }, [saving, onClose]);
  useEffect(() => {
    const fallback = fallbackFocusRef?.current;
    const previous = returnFocusRef?.current || fallback || document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const focusTitle = () => (title.current || dialog.current)?.focus();
    focusTitle();
    const keyboard = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (!latest.current.saving) latest.current.onClose();
      }
      if (event.key !== 'Tab') return;
      const targets = [...(dialog.current?.querySelectorAll(focusable) || [])].filter((node) => !node.hidden && node.getAttribute('aria-hidden') !== 'true');
      if (!targets.length) { event.preventDefault(); dialog.current?.focus(); return; }
      const first = targets[0]; const last = targets[targets.length - 1];
      const outsideTargets = !targets.includes(document.activeElement);
      if (event.shiftKey && (document.activeElement === first || outsideTargets)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || outsideTargets)) { event.preventDefault(); first.focus(); }
    };
    const retainFocus = (event) => { if (dialog.current && !dialog.current.contains(event.target)) focusTitle(); };
    document.addEventListener('keydown', keyboard);
    document.addEventListener('focusin', retainFocus);
    return () => {
      document.removeEventListener('keydown', keyboard);
      document.removeEventListener('focusin', retainFocus);
      document.body.style.overflow = overflow;
      const target = previous?.isConnected ? previous : fallback;
      if (target?.isConnected) target.focus();
    };
  }, [returnFocusRef, fallbackFocusRef]);
  return <div className="rehearsal-create-overlay">
    <section ref={dialog} className="rehearsal-create-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-busy={saving} tabIndex={-1}>
      <header className="rehearsal-create-dialog-heading"><h2 ref={title} id={titleId} tabIndex={-1}>새 합주</h2><button type="button" className="secondary-btn" aria-label="합주 추가 닫기" disabled={saving} onClick={onClose}>닫기</button></header>
      <div className="rehearsal-create-dialog-body">{children}</div>
    </section>
  </div>;
};
