import { useCallback, useEffect, useId, useRef } from 'react';
import { aggregateSlot, binaryStatus, PERIOD_LABELS, slotChoices, STATUS_LABELS } from './availabilityState.js';

export default function AvailabilityCell({ data, dayIndex, date, period, mode, disabled, pending, changed = false, popover, setPopover, onToggle, onDate }) {
  const root = useRef(null); const trigger = useRef(null); const id = useId();
  const suppressFocus = useRef(false);
  const choices = slotChoices(data, date, period, dayIndex);
  const own = choices.find((entry) => entry.membership_id === data.my_membership_id);
  const symbol = mode === 'mine' ? binaryStatus(own?.status) === 'AVAILABLE' ? 'O' : 'X' : aggregateSlot(choices);
  const active = mode === 'view' && popover?.kind !== 'candidate' && popover?.date === date && popover.period === period;
  const pinned = active && popover.pinned;
  const dirty = mode === 'mine' && changed;
  const description = mode === 'mine' ? STATUS_LABELS[symbol === 'O' ? 'AVAILABLE' : 'UNAVAILABLE'] : { O: '전원 가능', X: '전원 불가능', '△': '일부 가능·일부 불가능', '—': '집계할 활성 멤버 없음' }[symbol];
  const closeAndRestoreFocus = useCallback(() => {
    setPopover(null);
    if (trigger.current?.isConnected && document.activeElement !== trigger.current) {
      suppressFocus.current = true;
      trigger.current.focus();
      suppressFocus.current = false;
    }
  }, [setPopover]);
  useEffect(() => {
    if (!active) return undefined;
    const outside = (event) => { if (!root.current?.contains(event.target)) setPopover(null); };
    const escape = (event) => { if (event.key === 'Escape') { event.preventDefault(); closeAndRestoreFocus(); } };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [active, setPopover, closeAndRestoreFocus]);
  const inspect = () => { if (!popover?.pinned) setPopover({ date, period, pinned: false }); };
  return <div ref={root} className="availability-slot" onPointerEnter={(event) => { if (mode === 'view' && event.pointerType !== 'touch') inspect(); }} onPointerLeave={() => { if (active && !pinned) setPopover(null); }} onBlur={(event) => { if (active && !pinned && !event.currentTarget.contains(event.relatedTarget)) setPopover(null); }}>
    <button ref={trigger} type="button" data-availability-slot="true" data-date={date} data-period={period} disabled={disabled} aria-busy={Boolean(pending)} className={`availability-slot-button state-${symbol === 'O' ? 'available' : symbol === 'X' ? 'unavailable' : symbol === '—' ? 'empty' : 'mixed'}${pending ? ' is-pending' : ''}${dirty ? ' is-dirty' : ''}`} aria-label={`${date} ${PERIOD_LABELS[period]} ${mode === 'mine' ? '내 일정' : '멤버 선택'} ${symbol} ${description}${dirty ? ' 저장 전 변경' : ''}`} aria-pressed={mode === 'mine' ? symbol === 'O' : undefined} aria-expanded={mode === 'view' ? Boolean(active) : undefined} aria-describedby={active ? id : undefined} onFocus={() => { if (!suppressFocus.current && mode === 'view') inspect(); }} onClick={() => { onDate(date); if (mode === 'mine') onToggle(date, period); else setPopover(pinned ? null : { date, period, pinned: true }); }}>
      <span>{PERIOD_LABELS[period]}</span><b aria-hidden="true">{pending ? '…' : symbol}</b>
      {dirty && <span className="availability-dirty-marker" aria-hidden="true" />}
    </button>
    {active && <div id={id} role={pinned ? 'dialog' : 'tooltip'} aria-label={`${date} ${PERIOD_LABELS[period]} 멤버 선택 내역`} className="availability-slot-popover">
      <strong>{date} · {PERIOD_LABELS[period]}</strong>
      <ul>{choices.map((entry) => <li key={entry.membership_id}><span>{entry.display_name || '밴드 멤버'}{entry.membership_id === data.my_membership_id ? ' (나)' : ''}</span><b>{entry.status === 'AVAILABLE' ? 'O' : 'X'} {STATUS_LABELS[entry.status]}</b></li>)}</ul>
      {!choices.length && <p>활성 멤버가 없습니다.</p>}
      {pinned && <button type="button" className="secondary-btn" onClick={closeAndRestoreFocus}>선택 내역 닫기</button>}
    </div>}
  </div>;
}
