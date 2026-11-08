import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { activeAvailabilityMember, adjacentMonth, availabilityFingerprint, availabilityPath, batchDays, calendarWeeks, candidates, cellKey, monthDays, PERIOD_LABELS, PERIODS, seoulToday, validDay } from './availabilityState.js';
import { draftRows, isDraftChanged, previewDraft, reconcileDraft, toggleDraft } from './availabilityDraft.js';
import AvailabilityCell from './AvailabilityCell.jsx';
import AvailabilityCandidates from './AvailabilityCandidates.jsx';
import './AvailabilityCalendar.css';

export const RehearsalTabs = ({ band, availability = false, onLeave }) => <nav className="rehearsal-tabs" aria-label="합주 보기"><NavLink end onClick={(event) => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && onLeave && !onLeave()) event.preventDefault(); }} to={`/bands/${encodeURIComponent(band.slug)}/rehearsals`}>확정 일정</NavLink>{activeAvailabilityMember(band) && <NavLink aria-current={availability ? 'page' : undefined} to={`/bands/${encodeURIComponent(band.slug)}/availability`}>일정 조율</NavLink>}</nav>;
const resetTime = (value) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '';

export default function AvailabilityCalendar({ band, api, workspaceKey }) {
  return activeAvailabilityMember(band) ? <AvailabilityEditor key={`${workspaceKey}:${band.id}`} band={band} api={api} /> : <section className="workspace-page"><h1>일정 조율</h1><p>활성 밴드 멤버만 일정을 조회하고 등록할 수 있습니다.</p></section>;
}

function AvailabilityEditor({ band, api }) {
  const navigate = useNavigate(); const [month, setMonth] = useState(() => seoulToday().slice(0, 7));
  const dates = monthDays(month); const from = dates[0]; const to = dates.at(-1);
  const [data, setData] = useState(null); const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [mode, setMode] = useState('view'); const [popover, setPopover] = useState(null);
  const [drafts, setDrafts] = useState({}); const [needsVerification, setNeedsVerification] = useState(false);
  const recovery = useRef(null);
  const draftOrigin = useRef(null); const draftValues = useRef({});
  const storeDrafts = useCallback((value) => { draftValues.current = value; setDrafts(value); }, []);
  const dirty = Object.keys(drafts).length;
  const [selectedDate, setSelectedDate] = useState(() => seoulToday()); const [selectedMembers, setSelectedMembers] = useState(null);
  const [resetConfirm, setResetConfirm] = useState(false);
  const controller = useRef(null); const flight = useRef(false); const epoch = useRef(0); const mounted = useRef(true);
  const base = `/api/v2/bands/${Number(band.id)}/availability`;
  const reload = useCallback(async ({ preserveNotice = false, preserveData = false } = {}) => {
    const token = ++epoch.current; controller.current?.abort(); const next = new AbortController(); controller.current = next;
    setLoading(true); if (!preserveData) setData(null); setPopover(null); setResetConfirm(false);
    if (!preserveNotice) setNotice('');
    try {
      const result = await api.requestJson(availabilityPath(band.id, from, to), { signal: next.signal });
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return false;
      if (Number(result?.band_id) !== Number(band.id) || !Array.isArray(result.members) || !Array.isArray(result.days) || !Array.isArray(result.notes) || !Number.isInteger(result.generation)) throw new Error('Invalid response');
      if (result.days.some((day) => !validDay(day.date) || day.date < from || day.date > to || !Number.isInteger(day.revision) || day.revision < 0 || PERIODS.some((period) => !['AVAILABLE', 'UNAVAILABLE', 'UNKNOWN'].includes(day[period])))) throw new Error('Invalid day response');
      setData(result); setError('');
      const attempt = recovery.current || (draftOrigin.current && Object.keys(draftValues.current).length ? { baseline: draftOrigin.current, drafts: draftValues.current } : null);
      if (attempt) {
        const verifyingSave = Boolean(recovery.current);
        const outcome = attempt.conflict ? 'conflict' : reconcileDraft(attempt.baseline, attempt.drafts, result);
        if (outcome === 'confirmed') { storeDrafts({}); draftOrigin.current = null; setNotice('선택한 가능 일정을 최신 상태에서 확인했습니다.'); }
        else if (outcome === 'conflict') { storeDrafts({}); draftOrigin.current = null; setNotice('다른 기기에서 수정했거나 owner가 초기화했습니다. 최신 상태를 복구했으니 다시 선택해 주세요.'); }
        else { storeDrafts(attempt.drafts); draftOrigin.current = result; if (verifyingSave) setNotice('서버 상태는 변경 전과 같습니다. 초안을 유지했습니다. 확인 후 저장 버튼으로 다시 시도할 수 있습니다. 자동으로 재시도하지 않습니다.'); else setNotice((previous) => `${previous} 가능 일정 초안은 저장 전 상태로 유지됩니다.`.trim()); }
        recovery.current = null; setNeedsVerification(false);
      }
      setSelectedMembers((previous) => previous === null ? result.members.map((entry) => entry.membership_id) : previous.filter((id) => result.members.some((entry) => entry.membership_id === id)));
      return true;
    } catch (cause) {
      if (!next.signal.aborted && token === epoch.current && mounted.current) { setData(null); if ([401, 403, 404].includes(cause.status)) { storeDrafts({}); draftOrigin.current = null; recovery.current = null; setNeedsVerification(false); } setError([401, 403, 404].includes(cause.status) ? '이 밴드의 조율 일정을 볼 권한이 없습니다.' : '일정을 불러오지 못했습니다. 다시 시도해 주세요.'); }
      return false;
    } finally { if (!next.signal.aborted && token === epoch.current && mounted.current) setLoading(false); }
  }, [api, band.id, from, to, storeDrafts]);
  useEffect(() => { mounted.current = true; reload(); return () => { mounted.current = false; epoch.current += 1; controller.current?.abort(); }; }, [reload]);
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const discardDrafts = () => {
    if (flight.current) return false;
    if (dirty && !window.confirm('저장하지 않은 가능 일정 변경을 버릴까요? 취소하면 계속 편집할 수 있습니다.')) return false;
    storeDrafts({}); draftOrigin.current = null; recovery.current = null; setNeedsVerification(false); return true;
  };
  const requestReload = () => { if (!flight.current && (recovery.current || discardDrafts())) reload({ preserveNotice: Boolean(recovery.current) }); };
  const mutate = async (path, method, payload, success, { discard = false } = {}) => {
    if (flight.current || !data || loading || (discard && !discardDrafts())) return false;
    flight.current = true; setBusy(true); setError(''); setPopover(null); const token = epoch.current; const next = new AbortController(); controller.current = next;
    try {
      await api.requestJson(path, { method, signal: next.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ generation: data.generation, ...payload }) });
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return false;
      setNotice(success);
      const confirmed = await reload({ preserveNotice: true, preserveData: true });
      if (!confirmed && mounted.current) setNotice('저장 응답을 받았지만 최신 상태를 확인하지 못했습니다. 다시 불러온 뒤 확인해 주세요. 자동으로 재시도하지 않습니다.');
      return confirmed;
    } catch (cause) {
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return false;
      if ([401, 403, 404].includes(cause.status)) { setData(null); storeDrafts({}); draftOrigin.current = null; recovery.current = null; setNeedsVerification(false); setNotice(''); setError('이 밴드의 조율 일정을 수정할 권한이 없습니다.'); return false; }
      setNotice(cause.status === 409 ? '다른 기기에서 수정했거나 owner가 초기화했습니다. 최신 일정을 확인하고 다시 선택해 주세요.' : '처리 결과를 확인하지 못했습니다. 최신 상태를 확인해 주세요. 자동으로 재시도하지 않습니다.');
      await reload({ preserveNotice: true, preserveData: true }); return false;
    } finally { flight.current = false; if (mounted.current) setBusy(false); }
  };
  const toggle = (date, period) => {
    if (flight.current || !data || loading || needsVerification) return;
    setSelectedDate(date);
    const next = toggleDraft(data, draftValues.current, date, period);
    if (!draftOrigin.current) draftOrigin.current = data;
    if (!Object.keys(next).length) draftOrigin.current = null;
    storeDrafts(next);
  };
  const saveDrafts = async () => {
    if (flight.current || !data || loading || !dirty || needsVerification) return;
    const attempt = { baseline: draftOrigin.current || data, drafts };
    recovery.current = attempt;
    setNeedsVerification(true); flight.current = true; setBusy(true); setError(''); setNotice(''); setPopover(null);
    const token = epoch.current; const next = new AbortController(); controller.current = next;
    try {
      await api.requestJson(`${base}/me/batch`, { method: 'PUT', signal: next.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ generation: attempt.baseline.generation, days: draftRows(attempt.drafts) }) });
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return;
      await reload({ preserveNotice: true, preserveData: true });
    } catch (cause) {
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return;
      if ([401, 403, 404].includes(cause.status)) { setData(null); storeDrafts({}); draftOrigin.current = null; recovery.current = null; setNeedsVerification(false); setError('이 밴드의 조율 일정을 수정할 권한이 없습니다.'); return; }
      if (recovery.current) recovery.current.conflict = cause.status === 409;
      await reload({ preserveNotice: true, preserveData: true });
    } finally {
      flight.current = false;
      if (mounted.current) { setBusy(false); if (recovery.current) setNotice('저장 결과를 확인하지 못했습니다. 초안을 보관하고 있습니다. 다시 불러오기로 최신 상태를 확인하기 전에는 재전송하지 않습니다.'); }
    }
  };
  const role = String(band.membership?.role || '').toLowerCase(); const canCreate = role === 'owner' || role === 'admin';
  const dayIndex = useMemo(() => new Map((data?.days || []).map((day) => [`${day.membership_id}:${day.date}`, day])), [data]);
  const mineData = useMemo(() => previewDraft(data, drafts), [data, drafts]);
  const mineIndex = useMemo(() => new Map((mineData?.days || []).map((day) => [`${day.membership_id}:${day.date}`, day])), [mineData]);
  const ranked = data ? candidates(data, dates, selectedMembers || [], seoulToday()) : [];
  const shownDate = dates.includes(selectedDate) ? selectedDate : from;
  const candidateLink = async (candidate) => {
    if (flight.current || !canCreate || !discardDrafts()) return; flight.current = true; setBusy(true); setPopover(null); const next = new AbortController(); controller.current = next; const token = epoch.current;
    try {
      const current = await api.requestJson(availabilityPath(band.id, candidate.date, candidate.date), { signal: next.signal });
      if (next.signal.aborted || token !== epoch.current || !mounted.current) return;
      if (availabilityFingerprint(current, candidate.date) !== availabilityFingerprint(data, candidate.date)) { setNotice('가능 일정이 변경되었습니다. 최신 후보를 확인해 주세요.'); await reload({ preserveNotice: true, preserveData: true }); return; }
      const params = new URLSearchParams({ create: '1', date: candidate.date, period: candidate.period, availability: availabilityFingerprint(current, candidate.date) });
      navigate(`/bands/${encodeURIComponent(band.slug)}/rehearsals?${params}`);
    } catch (cause) { if (!next.signal.aborted && token === epoch.current && mounted.current) { if ([401, 403, 404].includes(cause.status)) setData(null); setError('최신 가능 일정을 확인하지 못했습니다. 다시 시도해 주세요.'); } }
    finally { flight.current = false; if (mounted.current) setBusy(false); }
  };
  const changeMonth = (offset) => { if (!discardDrafts()) return; setPopover(null); setMonth(adjacentMonth(month, offset)); };
  const changeMode = (next) => { if (next === mode || !discardDrafts()) return; setPopover(null); setMode(next); };
  return <section className="workspace-page availability-page" aria-labelledby="availability-title">
    <RehearsalTabs band={band} availability onLeave={discardDrafts} />
    <div className="workspace-page-heading"><div><p className="availability-eyebrow">FIND OUR NEXT SESSION</p><h1 id="availability-title">함께 가능한 날</h1><p>오전 · 오후 · 저녁, 가능한 시간만 가볍게 알려 주세요.</p></div><span className="availability-private">밴드 멤버 전용 · 한국 시간</span></div>
    <div className="availability-month"><button className="secondary-btn" aria-label="이전 달" disabled={busy} onClick={() => changeMonth(-1)}>←</button><h2>{month.replace('-', '년 ')}월</h2><button className="secondary-btn" aria-label="다음 달" disabled={busy} onClick={() => changeMonth(1)}>→</button><button className="secondary-btn" disabled={busy || loading} onClick={requestReload}>새로고침</button></div>
    {notice && <p className="availability-notice" role="status">{notice}</p>}{error && <div role="alert" className="workspace-error"><p>{error}</p>{!data && <button className="secondary-btn" disabled={busy || loading} onClick={requestReload}>다시 시도</button>}</div>}
    {loading && <p aria-live="polite">가능 일정을 불러오는 중입니다…</p>}
    {data && <>
      {data.last_reset_at && <p className="availability-notice">owner가 {resetTime(data.last_reset_at)}에 일정을 초기화했습니다. 가능한 일정을 다시 등록해 주세요.</p>}
      <div className="availability-layout"><section className="availability-panel" aria-labelledby="calendar-availability-title">
        <div className="availability-modes" role="group" aria-label="달력 모드"><button type="button" className="secondary-btn" aria-pressed={mode === 'view'} disabled={busy} onClick={() => changeMode('view')}>멤버 선택 여부 보기</button><button type="button" className="secondary-btn" aria-pressed={mode === 'mine'} disabled={busy} onClick={() => changeMode('mine')}>내 일정 선택하기</button></div>
        <h2 id="calendar-availability-title">{mode === 'view' ? '멤버 가능 시간' : '내 가능 시간'}</h2>
        <p className="workspace-muted">{mode === 'view' ? '전체 활성 멤버 기준: O 전원 가능 · X 전원 불가능 · △ 일부 가능. 시간대에 마우스를 올리거나 선택하면 멤버별 선택을 볼 수 있습니다.' : '시간대를 눌러 X 불가능 ↔ O 가능으로 선택한 뒤 저장을 눌러 주세요. 등록하지 않은 시간은 X 불가능입니다.'}</p>
        {mode === 'mine' && <div className="availability-draft-actions" role="group" aria-label="내 일정 변경 저장"><small className="availability-draft-legend">강조 테두리: 저장 전 변경</small><span aria-live="polite">{dirty ? `${dirty}일 변경 · 저장 전에는 멤버 보기와 후보에 반영되지 않습니다. 저장 전 변경은 페이지를 벗어나면 사라집니다.` : '저장하지 않은 변경이 없습니다.'}</span><button type="button" className="primary-btn" disabled={busy || loading || !dirty || needsVerification} onClick={saveDrafts}>{busy && needsVerification ? '저장 확인 중…' : '저장'}</button><button type="button" className="secondary-btn" disabled={busy || loading || !dirty} onClick={discardDrafts}>변경 취소</button></div>}
        <table className="availability-month-grid">
          <caption className="availability-sr">{month} {mode === 'view' ? '전체 멤버 선택 여부' : '내 일정 선택'} 달력. 날짜별 오전·오후·저녁.</caption>
          <thead><tr>{['일', '월', '화', '수', '목', '금', '토'].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
          <tbody>{calendarWeeks(month).map((week, index) => <tr key={index}>{week.map((date, column) => !date ? <td className="availability-empty-day" key={`empty-${column}`} aria-hidden="true" /> : <td key={date}><div className={`availability-day ${shownDate === date ? 'is-viewing' : ''}`} aria-current={date === seoulToday() ? 'date' : undefined}><span className="availability-day-number">{Number(date.slice(-2))}</span>{PERIODS.map((period) => <AvailabilityCell key={`${mode}:${month}:${date}:${period}`} data={mode === 'mine' ? mineData : data} dayIndex={mode === 'mine' ? mineIndex : dayIndex} date={date} period={period} mode={mode} disabled={busy || loading || needsVerification} changed={mode === 'mine' && isDraftChanged(data, drafts, date, period)} pending={busy && needsVerification && isDraftChanged(data, drafts, date, period)} popover={popover} setPopover={setPopover} onToggle={toggle} onDate={setSelectedDate} />)}</div></td>)}</tr>)}</tbody>
        </table>
        <details className="availability-reset-self"><summary>내 가능 시간 초기화</summary><p>현재 달의 내 상태를 모두 X 불가능으로 변경합니다. 개인 메모는 보존됩니다.</p><button className="secondary-btn" disabled={busy || loading} onClick={() => { if (window.confirm(`${band.name} · ${month} 내 가능 시간을 모두 불가능으로 바꿀까요? 개인 메모와 확정 합주는 유지됩니다.`)) mutate(`${base}/me/batch`, 'PUT', { days: batchDays(data, dates.flatMap((d) => PERIODS.map((p) => cellKey(d, p))), 'UNAVAILABLE') }, '이번 달 내 가능 시간을 불가능으로 초기화했습니다.', { discard: true }); }}>이번 달 내 상태 초기화</button></details>
      </section><aside className="availability-sidebar">
        <NoteEditor key={`${data.generation}:${shownDate}`} data={data} date={shownDate} busy={busy || loading} mutate={mutate} base={base} />
        <section className="availability-panel availability-candidate-panel"><h2>함께 가능한 후보</h2><details className="availability-candidate-filter"><summary>후보 계산 대상</summary><fieldset className="availability-member-picks" disabled={busy || loading}><legend className="availability-sr">후보 계산 대상</legend>{data.members.map((entry) => <label className="availability-check" key={entry.membership_id}><input type="checkbox" checked={(selectedMembers || []).includes(entry.membership_id)} onChange={(e) => { setPopover(null); setSelectedMembers((v) => e.target.checked ? [...(v || []), entry.membership_id] : (v || []).filter((id) => id !== entry.membership_id)); }} />{entry.display_name || '밴드 멤버'}</label>)}</fieldset></details>{!ranked.length ? <p>{selectedMembers?.length ? '이 달에는 오늘 이후의 후보 날짜가 없습니다.' : '조율할 멤버를 한 명 이상 선택해 주세요.'}</p> : <AvailabilityCandidates ranked={ranked} data={data} dayIndex={dayIndex} selectedMembers={selectedMembers || []} busy={busy || loading} canCreate={canCreate} popover={popover} setPopover={setPopover} onConfirm={candidateLink} />}</section>
        {data.can_reset && <section className="availability-owner-reset"><h2>새 일정 조율 시작</h2><p>owner 전용입니다. 현재 달뿐 아니라 이 밴드의 모든 조율 상태와 개인 메모(비공개 포함)를 지웁니다. 확정 합주와 콘텐츠는 유지됩니다.</p><button className="danger-btn" disabled={busy || loading} onClick={() => setResetConfirm(true)}>모두의 조율 일정 초기화</button>{resetConfirm && <div role="alertdialog" aria-label="전체 조율 일정 초기화 확인"><p><strong>{band.name}</strong>의 활성 멤버 {data.members.length}명과 탈퇴 멤버의 보관된 조율 자료를 포함한 전체 기간의 상태·메모가 삭제됩니다. 비공개 메모 본문은 owner에게 공개되지 않습니다.</p><p>전체 상태 등록 {data.reset_summary?.days ?? '확인 필요'}건 · 개인 메모 {data.reset_summary?.notes ?? '확인 필요'}건</p><p>이 작업은 되돌릴 수 없습니다. 모두 새로 등록해야 합니다.</p><button className="secondary-btn" disabled={busy} onClick={() => setResetConfirm(false)}>취소</button><button className="danger-btn" disabled={busy || !data.reset_summary} onClick={() => mutate(`${base}/reset`, 'POST', { confirm: true }, '모두의 조율 일정을 초기화했습니다. 가능한 일정을 다시 등록해 주세요.', { discard: true })}>확인하고 전체 초기화</button></div>}</section>}
      </aside></div>
    </>}
  </section>;
}
function NoteEditor({ data, date, busy, mutate, base }) {
  const [period, setPeriod] = useState('evening'); const existing = data.notes.find((note) => note.membership_id === data.my_membership_id && note.date === date && note.period === period);
  const [body, setBody] = useState(existing?.body || ''); const [visibility, setVisibility] = useState(existing?.visibility || 'PRIVATE');
  useEffect(() => { setBody(existing?.body || ''); setVisibility(existing?.visibility || 'PRIVATE'); }, [existing?.body, existing?.visibility, period]); // eslint-disable-line react-hooks/set-state-in-effect
  const others = data.notes.filter((note) => note.date === date && note.body && note.visibility === 'BAND' && note.membership_id !== data.my_membership_id);
  const path = `${base}/me/notes/${date}/${period}`; const count = [...body].length;
  return <details className="availability-panel availability-notes"><summary>선택 개인 일정 메모</summary><p className="workspace-muted">등록하지 않아도 됩니다. 기본 비공개이며 owner 전체 초기화 때 함께 삭제되는 조율용 메모입니다.</p><label>메모 시간대<select value={period} onChange={(e) => setPeriod(e.target.value)} disabled={busy}>{PERIODS.map((p) => <option key={p} value={p}>{PERIOD_LABELS[p]}</option>)}</select></label><label>개인 일정 (선택)<textarea value={body} onChange={(e) => setBody(e.target.value)} disabled={busy} aria-describedby="note-counter" /></label><small id="note-counter">{count}/200자 · 전화번호 등 민감한 정보는 적지 마세요.</small><label className="availability-check"><input type="checkbox" checked={visibility === 'BAND'} disabled={busy} onChange={(e) => setVisibility(e.target.checked ? 'BAND' : 'PRIVATE')} />이 메모를 같은 밴드 멤버에게 공유</label><div className="availability-toolbar"><button className="secondary-btn" disabled={busy || !body.trim() || count > 200} onClick={() => mutate(path, 'PUT', { revision: existing?.revision || 0, body: body.trim(), visibility }, '개인 일정 메모를 저장했습니다.')}>메모 저장</button><button className="secondary-btn" disabled={busy || !existing?.body} onClick={() => mutate(path, 'DELETE', { revision: existing.revision }, '개인 일정 메모를 삭제했습니다.')}>메모 삭제</button></div>{others.length > 0 && <div className="availability-shared-notes"><h3>멤버가 공유한 메모</h3>{others.map((note) => <article key={`${note.membership_id}:${note.period}`}><strong>{data.members.find((entry) => entry.membership_id === note.membership_id)?.display_name || '밴드 멤버'} · {PERIOD_LABELS[note.period]}</strong><p>{note.body}</p></article>)}</div>}</details>;
}
