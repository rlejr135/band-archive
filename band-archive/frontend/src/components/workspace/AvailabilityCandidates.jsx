import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { candidateDates, PERIOD_LABELS, PERIODS, slotChoices, STATUS_LABELS } from './availabilityState.js';

export default function AvailabilityCandidates({ ranked, data, dayIndex, selectedMembers, busy, canCreate, popover, setPopover, onConfirm }) {
  const filterKey = [...selectedMembers].sort((a, b) => a - b).join(',');
  return <div className="availability-candidate-dates" role="group" aria-label="합주 후보 날짜">{candidateDates(ranked).map((group) => <CandidateDate key={`${group.date}:${filterKey}`} group={group} data={data} dayIndex={dayIndex} selectedMembers={selectedMembers} busy={busy} canCreate={canCreate} popover={popover} setPopover={setPopover} onConfirm={onConfirm} />)}</div>;
}

function CandidateDate({ group, data, dayIndex, selectedMembers, busy, canCreate, popover, setPopover, onConfirm }) {
  const root = useRef(null); const trigger = useRef(null); const suppressFocus = useRef(false); const id = useId();
  const [period, setPeriod] = useState(group.best.period);
  const [placement, setPlacement] = useState({});
  const active = !busy && popover?.kind === 'candidate' && popover.date === group.date;
  const pinned = active && popover.pinned;
  const choices = slotChoices(data, group.date, period, dayIndex).filter((entry) => selectedMembers.includes(entry.membership_id));
  const selected = group.periods.find((entry) => entry.period === period);
  const close = useCallback(() => {
    setPopover(null);
    if (root.current?.contains(document.activeElement) && trigger.current?.isConnected && document.activeElement !== trigger.current) {
      suppressFocus.current = true; trigger.current.focus(); suppressFocus.current = false;
    }
  }, [setPopover]);
  useEffect(() => {
    if (!active) return undefined;
    const outside = (event) => { if (!root.current?.contains(event.target)) setPopover(null); };
    const escape = (event) => { if (event.key === 'Escape') { event.preventDefault(); close(); } };
    const resize = () => setPopover(null);
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    window.addEventListener('resize', resize);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); window.removeEventListener('resize', resize); };
  }, [active, setPopover, close]);
  const open = (pin) => {
    if (busy) return;
    if (!active) setPeriod(group.best.period);
    const rect = root.current?.getBoundingClientRect();
    if (rect) {
      const width = Math.min(320, window.innerWidth - 32);
      const above = window.innerHeight - rect.bottom < 300 && rect.top > window.innerHeight / 2;
      setPlacement({ width, left: Math.max(16 - rect.left, Math.min(0, window.innerWidth - 16 - rect.left - width)), maxHeight: Math.max(0, Math.min(420, window.innerHeight - 32, (above ? rect.top : window.innerHeight - rect.bottom) - 16)), ...(above ? { bottom: '100%' } : { top: '100%' }) });
    }
    setPopover({ kind: 'candidate', date: group.date, pinned: pin });
  };
  return <div ref={root} className="availability-candidate-date" onPointerEnter={(event) => { if (event.pointerType !== 'touch' && !popover?.pinned && !active) open(false); }} onPointerLeave={() => { if (active && !pinned && !root.current?.contains(document.activeElement)) setPopover(null); }} onBlur={(event) => { if (active && !pinned && !event.currentTarget.contains(event.relatedTarget)) setPopover(null); }}>
    <button ref={trigger} type="button" className="secondary-btn availability-candidate-date-button" aria-label={`${group.date} 후보 시간대별 멤버 가능 여부 보기`} aria-haspopup="dialog" aria-expanded={Boolean(active)} aria-controls={active ? id : undefined} disabled={busy} onFocus={() => { if (!suppressFocus.current && !active) open(false); }} onClick={() => { if (pinned) setPopover(null); else open(true); }}>{group.date}</button>
    {active && <section id={id} role="dialog" aria-modal="false" aria-label={`${group.date} 후보 상세`} className="availability-candidate-detail" style={placement}>
      <div className="availability-candidate-detail-heading"><strong>{group.date}</strong><button type="button" className="secondary-btn" aria-label="후보 상세 닫기" onClick={close}>닫기</button></div>
      <div className="availability-candidate-periods" role="group" aria-label="후보 시간대">{PERIODS.map((value) => { const result = group.periods.find((entry) => entry.period === value); return <button type="button" key={value} className="secondary-btn" aria-pressed={period === value} onClick={() => setPeriod(value)}>{PERIOD_LABELS[value]}<small>가능 {result?.available ?? 0}/{result?.total ?? 0}</small></button>; })}</div>
      <p>{PERIOD_LABELS[period]} · 후보 계산 대상으로 선택한 멤버 {choices.length}명</p>
      <p className="workspace-muted">정확한 시작 시각이나 연속 가능한 시간을 보장하지 않는 조율 자료입니다.</p>
      <ul className="availability-candidate-members">{choices.map((entry) => <li key={entry.membership_id}><span>{entry.display_name || '밴드 멤버'}{entry.membership_id === data.my_membership_id ? ' (나)' : ''}</span><b>{entry.status === 'AVAILABLE' ? 'O' : 'X'} {STATUS_LABELS[entry.status]}</b></li>)}</ul>
      {canCreate && <><p className="workspace-muted">선택 시간대로 합주 추가 화면을 엽니다.</p><button type="button" className="primary-btn availability-candidate-confirm" disabled={busy || !selected} aria-label={`${group.date} ${PERIOD_LABELS[period]} 확인`} onClick={() => onConfirm(selected)}>확인</button></>}
    </section>}
  </div>;
}
