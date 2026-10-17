import { useCallback, useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { activeAvailabilityMember, adjacentMonth, availabilityFingerprint, availabilityPath, batchDays, calendarWeeks, candidates, cellKey, inclusiveDates, monthDays, ownDay, PERIOD_LABELS, PERIODS, seoulToday, STATUS_LABELS } from './availabilityState.js';
import './AvailabilityCalendar.css';

export const RehearsalTabs = ({ band, availability = false }) => <nav className="rehearsal-tabs" aria-label="합주 보기"><NavLink end to={`/bands/${encodeURIComponent(band.slug)}/rehearsals`}>확정 일정</NavLink>{activeAvailabilityMember(band) && <NavLink aria-current={availability ? 'page' : undefined} to={`/bands/${encodeURIComponent(band.slug)}/availability`}>일정 조율</NavLink>}</nav>;
const resetTime = (value) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '';

export default function AvailabilityCalendar({ band, api, workspaceKey }) {
  // The parent keys this component by account + membership + band identity.
  const member = activeAvailabilityMember(band);
  return member ? <AvailabilityEditor key={workspaceKey} band={band} api={api} /> : <section className="workspace-page"><h1>일정 조율</h1><p>활성 밴드 멤버만 일정을 조회하고 등록할 수 있습니다.</p></section>;
}

function AvailabilityEditor({ band, api }) {
  const navigate = useNavigate(); const [month, setMonth] = useState(() => seoulToday().slice(0, 7));
  const dates = monthDays(month); const from = dates[0]; const to = dates.at(-1);
  const [data, setData] = useState(null); const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [selectedDays, setSelectedDays] = useState([]);
  const [applyOpen, setApplyOpen] = useState(false); const [selectedDate, setSelectedDate] = useState(() => seoulToday());
  const [touchMode, setTouchMode] = useState(false); const [selectedMembers, setSelectedMembers] = useState(null);
  const [resetConfirm, setResetConfirm] = useState(false);
  const controller = useRef(null); const flight = useRef(false); const epoch = useRef(0); const mounted = useRef(true); const anchor = useRef(null); const dragging = useRef(false); const pointerKind = useRef('mouse');
  const base = `/api/v2/bands/${Number(band.id)}/availability`;
  const reload = useCallback(async ({ preserveNotice = false } = {}) => {
    const token = ++epoch.current; controller.current?.abort(); const next = new AbortController(); controller.current = next;
    setLoading(true); setData(null); setSelectedDays([]); setApplyOpen(false); setResetConfirm(false); anchor.current = null; dragging.current = false;
    if (!preserveNotice) setNotice('');
    try {
      const result = await api.requestJson(availabilityPath(band.id, from, to), { signal: next.signal });
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return;
      if (Number(result?.band_id) !== Number(band.id) || !Array.isArray(result.members) || !Array.isArray(result.days) || !Array.isArray(result.notes) || !Number.isInteger(result.generation)) throw new Error('Invalid response');
      setData(result); setError('');
      setSelectedMembers((previous) => previous === null ? result.members.map((entry) => entry.membership_id) : previous.filter((id) => result.members.some((entry) => entry.membership_id === id)));
    } catch (cause) { if (!next.signal.aborted && token === epoch.current && mounted.current) setError(cause.status === 403 || cause.status === 404 ? '이 밴드의 조율 일정을 볼 권한이 없습니다.' : '일정을 불러오지 못했습니다. 다시 시도해 주세요.'); }
    finally { if (!next.signal.aborted && token === epoch.current && mounted.current) setLoading(false); }
  }, [api, band.id, from, to]);
  useEffect(() => { mounted.current = true; reload(); return () => { mounted.current = false; epoch.current += 1; controller.current?.abort(); }; }, [reload]);
  useEffect(() => { const release = () => { dragging.current = false; }; window.addEventListener('pointerup', release); window.addEventListener('pointercancel', release); return () => { window.removeEventListener('pointerup', release); window.removeEventListener('pointercancel', release); }; }, []);
  const mutate = async (path, method, payload, success) => {
    if (flight.current || !data || loading) return false;
    flight.current = true; setBusy(true); setError(''); const token = epoch.current; const next = new AbortController(); controller.current = next;
    try {
      await api.requestJson(path, { method, signal: next.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ generation: data.generation, ...payload }) });
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return false;
      setNotice(success); await reload({ preserveNotice: true }); return true;
    } catch (cause) {
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return false;
      setSelectedDays([]); anchor.current = null;
      setNotice(cause.status === 409 ? '다른 기기에서 수정했거나 owner가 초기화했습니다. 최신 일정을 확인하고 다시 선택해 주세요.' : '처리 결과를 확인하지 못했습니다. 최신 상태를 확인해 주세요. 자동으로 재시도하지 않습니다.');
      await reload({ preserveNotice: true }); return false;
    } finally { flight.current = false; if (mounted.current) setBusy(false); }
  };
  const saveDays = (periods, status) => {
    if (!selectedDays.length || !periods.length || busy) return;
    const cells = selectedDays.flatMap((date) => periods.map((period) => cellKey(date, period)));
    mutate(`${base}/me/batch`, 'PUT', { days: batchDays(data, cells, status) }, `${selectedDays.length}일의 ${periods.map((p) => PERIOD_LABELS[p]).join('·')}${periods.at(-1) === 'afternoon' ? '를' : '을'} ${STATUS_LABELS[status]}${status === 'UNAVAILABLE' ? '로' : '으로'} 저장했습니다.`);
  };
  const enterDay = (date) => { if (dragging.current && anchor.current) setSelectedDays(inclusiveDates(dates, anchor.current, date)); };
  const startDay = (event, date) => {
    pointerKind.current = event.pointerType || 'mouse';
    if (busy || loading || (event.pointerType === 'touch' && !touchMode) || (event.button !== undefined && event.button !== 0)) return;
    if (event.shiftKey && anchor.current) { event.preventDefault(); setSelectedDays(inclusiveDates(dates, anchor.current, date)); setSelectedDate(date); dragging.current = false; return; }
    event.preventDefault(); dragging.current = true; anchor.current = date; setSelectedDays([date]); setSelectedDate(date);
  };
  const moveTouch = (event) => { if (!touchMode || !dragging.current || event.pointerType !== 'touch') return; const element = document.elementFromPoint?.(event.clientX, event.clientY)?.closest('[data-availability-day]'); if (element) enterDay(element.dataset.date); };
  const pickDay = (date, extend = false) => {
    if (extend && anchor.current) setSelectedDays(inclusiveDates(dates, anchor.current, date));
    else { anchor.current = date; setSelectedDays([date]); }
    setSelectedDate(date);
  };
  const chooseKeyboard = (event, date) => {
    const index = dates.indexOf(date); const offset = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
    if (offset !== undefined) {
      event.preventDefault(); const target = dates[index + offset]; if (!target) return;
      document.querySelector(`[data-availability-day][data-date="${target}"]`)?.focus();
      if (event.shiftKey) { if (!anchor.current) anchor.current = date; pickDay(target, true); } return;
    }
    if (event.key !== ' ' && event.key !== 'Enter') return;
    event.preventDefault(); pickDay(date, event.shiftKey);
  };
  const role = String(band.membership?.role || '').toLowerCase(); const canCreate = role === 'owner' || role === 'admin';
  const ranked = data ? candidates(data, dates, selectedMembers || [], seoulToday()) : [];
  const shownDate = dates.includes(selectedDate) ? selectedDate : from;
  const candidateLink = async (candidate) => {
    if (flight.current || !canCreate) return; flight.current = true; setBusy(true); const next = new AbortController(); controller.current = next; const token = epoch.current;
    try {
      const current = await api.requestJson(availabilityPath(band.id, candidate.date, candidate.date), { signal: next.signal });
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return;
      if (availabilityFingerprint(current, candidate.date) !== availabilityFingerprint(data, candidate.date)) { setNotice('가능 일정이 변경되었습니다. 최신 후보를 확인해 주세요.'); await reload({ preserveNotice: true }); return; }
      const params = new URLSearchParams({ create: '1', date: candidate.date, period: candidate.period, availability: availabilityFingerprint(current, candidate.date) });
      navigate(`/bands/${encodeURIComponent(band.slug)}/rehearsals?${params}`);
    } catch { if (!next.signal.aborted && mounted.current) setError('최신 가능 일정을 확인하지 못했습니다. 다시 시도해 주세요.'); }
    finally { flight.current = false; if (mounted.current) setBusy(false); }
  };
  return <section className="workspace-page availability-page" aria-labelledby="availability-title">
    <RehearsalTabs band={band} availability />
    <div className="workspace-page-heading"><div><p className="availability-eyebrow">FIND OUR NEXT SESSION</p><h1 id="availability-title">함께 가능한 날</h1><p>오전 · 오후 · 저녁, 가능한 시간만 가볍게 알려 주세요.</p></div><span className="availability-private">밴드 멤버 전용 · 한국 시간</span></div>
    <div className="availability-month"><button className="secondary-btn" aria-label="이전 달" disabled={busy} onClick={() => setMonth(adjacentMonth(month, -1))}>←</button><h2>{month.replace('-', '년 ')}월</h2><button className="secondary-btn" aria-label="다음 달" disabled={busy} onClick={() => setMonth(adjacentMonth(month, 1))}>→</button><button className="secondary-btn" disabled={busy || loading} onClick={() => reload()}>새로고침</button></div>
    {notice && <p className="availability-notice" role="status">{notice}</p>}{error && <div role="alert" className="workspace-error"><p>{error}</p>{!data && <button className="secondary-btn" disabled={busy || loading} onClick={() => reload()}>다시 시도</button>}</div>}
    {loading && <p aria-live="polite">가능 일정을 불러오는 중입니다…</p>}
    {data && <>
      {data.last_reset_at && <p className="availability-notice">owner가 {resetTime(data.last_reset_at)}에 일정을 초기화했습니다. 가능한 일정을 다시 등록해 주세요.</p>}
      <div className="availability-layout"><section className="availability-panel" aria-labelledby="my-availability-title">
        <h2 id="my-availability-title">내 가능 시간</h2>
        <p className="workspace-muted">달력에서 날짜를 드래그한 뒤 적용을 눌러 시간대를 골라 주세요. 미정은 가능으로 계산하지 않습니다.</p>
        <label className="availability-check"><input type="checkbox" checked={touchMode} onChange={(event) => setTouchMode(event.target.checked)} />터치 범위 선택 모드 <small>끄면 시작·끝 날짜를 차례로 탭하고, 켜면 드래그로 여러 날짜를 선택합니다.</small></label>
        <p className="availability-keyboard-hint">키보드: 방향키로 이동 · Space/Enter로 선택 · Shift와 함께 범위 선택</p>
        <table className={`availability-month-grid ${touchMode ? 'touch-selecting' : ''}`} onPointerMove={moveTouch}>
          <caption className="availability-sr">{month} 내 가능 시간 달력. 날짜를 선택한 후 적용 버튼을 누르세요.</caption>
          <thead><tr>{['일', '월', '화', '수', '목', '금', '토'].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
          <tbody>{calendarWeeks(month).map((week, index) => <tr key={index}>{week.map((date, column) => {
            if (!date) return <td className="availability-empty-day" key={`empty-${column}`} aria-hidden="true" />;
            const own = ownDay(data, date);
            return <td key={date}><button type="button" data-availability-day="true" data-date={date} aria-label={`${date} 일정 선택`} aria-pressed={selectedDays.includes(date)} aria-current={date === seoulToday() ? 'date' : undefined} disabled={busy}
              className={`availability-day ${selectedDays.includes(date) ? 'is-selected' : ''} ${shownDate === date ? 'is-viewing' : ''}`}
              onPointerDown={(event) => startDay(event, date)} onPointerEnter={() => enterDay(date)} onKeyDown={(event) => chooseKeyboard(event, date)}
              onClick={(event) => { if (event.detail === 0) return; if (pointerKind.current === 'touch' && !touchMode) pickDay(date, event.shiftKey || (anchor.current !== null && selectedDays.length === 1 && selectedDays[0] !== date)); }}>
              <span className="availability-day-number">{Number(date.slice(-2))}</span>
              <span className="availability-day-states">{PERIODS.map((period) => <span className={`state-${own[period].toLowerCase()}`} key={period} title={`${PERIOD_LABELS[period]} ${STATUS_LABELS[own[period]]}`}><span>{PERIOD_LABELS[period]}</span><b aria-hidden="true">{own[period] === 'AVAILABLE' ? '○' : own[period] === 'UNAVAILABLE' ? '×' : '—'}</b><span className="availability-sr">{STATUS_LABELS[own[period]]}</span></span>)}</span>
            </button></td>;
          })}</tr>)}</tbody>
        </table>
        <div className="availability-selection-bar">
          <span aria-live="polite">{selectedDays.length ? `${selectedDays.length}일 선택 · ${selectedDays[0].slice(5)} ~ ${selectedDays.at(-1).slice(5)}` : '날짜를 선택해 주세요'}</span>
          <button className="primary-btn" disabled={busy || !selectedDays.length} onClick={() => setApplyOpen(true)}>적용</button>
          <button className="secondary-btn" disabled={busy || !selectedDays.length} onClick={() => { setSelectedDays([]); anchor.current = null; }}>선택 취소</button>
        </div>
        {applyOpen && <ApplyAvailabilityDialog dates={selectedDays} busy={busy} onClose={() => { if (!busy) setApplyOpen(false); }} onSave={saveDays} />}
        <details className="availability-reset-self"><summary>내 가능 시간 초기화</summary><p>현재 달의 내 상태만 모두 미정으로 변경합니다. 개인 메모는 보존됩니다.</p><button className="secondary-btn" disabled={busy} onClick={() => { if (window.confirm(`${band.name} · ${month} 내 가능 시간을 모두 미정으로 바꿀까요? 개인 메모와 확정 합주는 유지됩니다.`)) mutate(`${base}/me/batch`, 'PUT', { days: batchDays(data, dates.flatMap((d) => PERIODS.map((p) => cellKey(d, p))), 'UNKNOWN') }, '이번 달 내 가능 시간을 미정으로 초기화했습니다.'); }}>이번 달 내 상태 초기화</button></details>
      </section><aside className="availability-sidebar">
        <section className="availability-panel"><h2>{shownDate} 멤버 일정</h2><div className="availability-member-list">{data.members.map((entry) => { const day = data.days.find((d) => d.membership_id === entry.membership_id && d.date === shownDate); return <div key={entry.membership_id}><strong>{entry.display_name || '밴드 멤버'}{entry.membership_id === data.my_membership_id ? ' (나)' : ''}</strong><div>{PERIODS.map((p) => <span key={p} className={`state-${(day?.[p] || 'UNKNOWN').toLowerCase()}`}>{PERIOD_LABELS[p]} {STATUS_LABELS[day?.[p] || 'UNKNOWN']}</span>)}</div></div>; })}</div></section>
        <NoteEditor key={`${data.generation}:${shownDate}`} data={data} date={shownDate} busy={busy} mutate={mutate} base={base} />
        <section className="availability-panel"><h2>함께 가능한 후보</h2><p className="workspace-muted">정확한 시작 시각이나 연속 가능한 시간을 보장하지 않는 조율 자료입니다.</p><fieldset className="availability-member-picks"><legend>조율할 멤버</legend>{data.members.map((entry) => <label className="availability-check" key={entry.membership_id}><input type="checkbox" checked={(selectedMembers || []).includes(entry.membership_id)} onChange={(e) => setSelectedMembers((v) => e.target.checked ? [...(v || []), entry.membership_id] : (v || []).filter((id) => id !== entry.membership_id))} />{entry.display_name || '밴드 멤버'}</label>)}</fieldset>{!ranked.length ? <p>{selectedMembers?.length ? '이 달에는 오늘 이후의 후보 날짜가 없습니다.' : '조율할 멤버를 한 명 이상 선택해 주세요.'}</p> : <ol className="availability-candidates">{ranked.slice(0, 6).map((candidate) => <li key={cellKey(candidate.date, candidate.period)}><strong>{candidate.date} · {PERIOD_LABELS[candidate.period]}</strong>{candidate.all && <span className="availability-all">선택 멤버 전원 가능</span>}<span>가능 {candidate.available} · 불가 {candidate.unavailable} · 미정 {candidate.unknown}</span>{canCreate && <button className="secondary-btn" disabled={busy} onClick={() => candidateLink(candidate)}>이 시간대로 합주 만들기</button>}</li>)}</ol>}</section>
        {data.can_reset && <section className="availability-owner-reset"><h2>새 일정 조율 시작</h2><p>owner 전용입니다. 현재 달뿐 아니라 이 밴드의 모든 조율 상태와 개인 메모(비공개 포함)를 지웁니다. 확정 합주와 콘텐츠는 유지됩니다.</p><button className="danger-btn" disabled={busy} onClick={() => setResetConfirm(true)}>모두의 조율 일정 초기화</button>{resetConfirm && <div role="alertdialog" aria-label="전체 조율 일정 초기화 확인"><p><strong>{band.name}</strong>의 활성 멤버 {data.members.length}명과 탈퇴 멤버의 보관된 조율 자료를 포함한 전체 기간의 상태·메모가 삭제됩니다. 비공개 메모 본문은 owner에게 공개되지 않습니다.</p><p>전체 상태 등록 {data.reset_summary?.days ?? '확인 필요'}건 · 개인 메모 {data.reset_summary?.notes ?? '확인 필요'}건</p><p>이 작업은 되돌릴 수 없습니다. 모두 새로 등록해야 합니다.</p><button className="secondary-btn" disabled={busy} onClick={() => setResetConfirm(false)}>취소</button><button className="danger-btn" disabled={busy || !data.reset_summary} onClick={() => mutate(`${base}/reset`, 'POST', { confirm: true }, '모두의 조율 일정을 초기화했습니다. 가능한 일정을 다시 등록해 주세요.')}>확인하고 전체 초기화</button></div>}</section>}
      </aside></div>
    </>}
  </section>;
}

function ApplyAvailabilityDialog({ dates, busy, onClose, onSave }) {
  const [periods, setPeriods] = useState([]); const [status, setStatus] = useState('AVAILABLE'); const dialog = useRef(null);
  useEffect(() => {
    const previous = document.activeElement; const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden'; dialog.current?.querySelector('button')?.focus();
    return () => { document.body.style.overflow = overflow; if (previous?.isConnected) previous.focus(); };
  }, []);
  const keyboard = (event) => {
    if (event.key === 'Escape') { event.preventDefault(); if (!busy) onClose(); return; }
    if (event.key !== 'Tab') return;
    const buttons = [...dialog.current.querySelectorAll('button:not(:disabled)')];
    if (!buttons.length) { event.preventDefault(); dialog.current.focus(); return; }
    const first = buttons[0]; const last = buttons.at(-1);
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  return <div className="availability-dialog-overlay" onPointerDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section ref={dialog} className="availability-apply-dialog" role="dialog" aria-modal="true" aria-labelledby="availability-apply-title" aria-describedby="availability-apply-description" tabIndex={-1} onKeyDown={keyboard}>
      <p className="availability-eyebrow">SET YOUR AVAILABILITY</p><h2 id="availability-apply-title">선택한 날짜에 적용</h2>
      <p id="availability-apply-description">{dates[0]} ~ {dates.at(-1)} · {dates.length}일<br />시간대를 여러 개 선택할 수 있어요. 선택하지 않은 시간대는 유지됩니다.</p>
      <fieldset disabled={busy}><legend>시간대 · 복수 선택</legend><div className="availability-option-tabs" role="group" aria-label="적용할 시간대">{PERIODS.map((period) => <button type="button" key={period} aria-pressed={periods.includes(period)} onClick={() => setPeriods((current) => current.includes(period) ? current.filter((p) => p !== period) : PERIODS.filter((p) => p === period || current.includes(p)))}>{PERIOD_LABELS[period]}<span aria-hidden="true">{periods.includes(period) ? '✓' : '+'}</span></button>)}</div></fieldset>
      <fieldset disabled={busy}><legend>가능 여부 · 하나 선택</legend><div className="availability-option-tabs availability-status-tabs" role="group" aria-label="적용할 상태">{Object.entries(STATUS_LABELS).map(([value, label]) => <button type="button" key={value} aria-pressed={status === value} onClick={() => setStatus(value)}>{label}</button>)}</div></fieldset>
      {!periods.length && <p className="availability-dialog-help">오전·오후·저녁 중 한 개 이상 선택해 주세요.</p>}
      <div className="form-actions"><button type="button" className="secondary-btn" disabled={busy} onClick={onClose}>취소</button><button type="button" className="primary-btn" disabled={busy || !periods.length} onClick={() => onSave(periods, status)}>{busy ? '저장 중…' : '저장'}</button></div>
    </section>
  </div>;
}

function NoteEditor({ data, date, busy, mutate, base }) {
  const [period, setPeriod] = useState('evening'); const existing = data.notes.find((note) => note.membership_id === data.my_membership_id && note.date === date && note.period === period);
  const [body, setBody] = useState(existing?.body || ''); const [visibility, setVisibility] = useState(existing?.visibility || 'PRIVATE');
  useEffect(() => { setBody(existing?.body || ''); setVisibility(existing?.visibility || 'PRIVATE'); }, [existing?.body, existing?.visibility, period]); // eslint-disable-line react-hooks/set-state-in-effect
  const others = data.notes.filter((note) => note.date === date && note.body && note.visibility === 'BAND' && note.membership_id !== data.my_membership_id);
  const path = `${base}/me/notes/${date}/${period}`; const count = [...body].length;
  return <details className="availability-panel availability-notes"><summary>선택 개인 일정 메모</summary><p className="workspace-muted">등록하지 않아도 됩니다. 기본 비공개이며 owner 전체 초기화 때 함께 삭제되는 조율용 메모입니다.</p><label>메모 시간대<select value={period} onChange={(e) => setPeriod(e.target.value)} disabled={busy}>{PERIODS.map((p) => <option key={p} value={p}>{PERIOD_LABELS[p]}</option>)}</select></label><label>개인 일정 (선택)<textarea value={body} onChange={(e) => setBody(e.target.value)} disabled={busy} aria-describedby="note-counter" /></label><small id="note-counter">{count}/200자 · 전화번호 등 민감한 정보는 적지 마세요.</small><label className="availability-check"><input type="checkbox" checked={visibility === 'BAND'} disabled={busy} onChange={(e) => setVisibility(e.target.checked ? 'BAND' : 'PRIVATE')} />이 메모를 같은 밴드 멤버에게 공유</label><div className="availability-toolbar"><button className="secondary-btn" disabled={busy || !body.trim() || count > 200} onClick={() => mutate(path, 'PUT', { revision: existing?.revision || 0, body: body.trim(), visibility }, '개인 일정 메모를 저장했습니다.')}>메모 저장</button><button className="secondary-btn" disabled={busy || !existing?.body} onClick={() => mutate(path, 'DELETE', { revision: existing.revision }, '개인 일정 메모를 삭제했습니다.')}>메모 삭제</button></div>{others.length > 0 && <div className="availability-shared-notes"><h3>멤버가 공유한 메모</h3>{others.map((note) => <article key={`${note.membership_id}:${note.period}`}><strong>{data.members.find((entry) => entry.membership_id === note.membership_id)?.display_name || '밴드 멤버'} · {PERIOD_LABELS[note.period]}</strong><p>{note.body}</p></article>)}</div>}</details>;
}
