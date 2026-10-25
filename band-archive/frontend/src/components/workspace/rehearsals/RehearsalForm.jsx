import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useServicePolicy } from '../../../context/ServicePolicyContext.jsx';
import { reviewPublicVisibility } from '../../../services/uploadPolicy.js';
import { bandWorkspacePaths, rehearsalPayload } from '../../../services/bandWorkspaceApi.js';
import { normalizeRehearsalType, REHEARSAL_TYPES } from '../../../services/rehearsalTypes.js';
import { availabilityFingerprint, availabilityPath, PERIOD_LABELS, validDay } from '../availabilityState.js';
import BandPlacePicker from '../BandPlacePicker.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { requestOptions } from '../workspaceReadSupport.js';

export const RehearsalForm = ({ item, onDone, onCancel }) => {
  const { config } = useServicePolicy();
  const { band, api, workspaceKey } = useWorkspace();
  const [saving, setSaving] = useState(false); const savingRef = useRef(false); const [error, setError] = useState(''); const [songs, setSongs] = useState([]);
  const [resultUnknown, setResultUnknown] = useState(false);
  const [searchParams] = useSearchParams();
  const candidate = useRef(!item && validDay(searchParams.get('date')) && Object.hasOwn(PERIOD_LABELS, searchParams.get('period')) ? { date: searchParams.get('date'), period: searchParams.get('period'), fingerprint: searchParams.get('availability') || '' } : null);
  const saveController = useRef(null); const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; saveController.current?.abort(); }; }, []);
  const originalRehearsalType = useRef(item ? normalizeRehearsalType(item.rehearsal_type) : 'REGULAR');
  const initial = { title: item?.title || '', date: item?.date || candidate.current?.date || '', start_date: item?.start_date || '', end_date: item?.end_date || '', time: item?.time || '', location: item?.location || '', memo: item?.memo || '', color: item?.color || '', rehearsal_type: item ? normalizeRehearsalType(item.rehearsal_type) : 'REGULAR', visibility: item?.visibility || band.default_visibility || 'MEMBERS', song_ids: item?.songs?.map((song) => String(song.id)) || [] };
  const [value, setValue] = useState(initial);
  useEffect(() => { const controller = new AbortController(); api.requestJson(bandWorkspacePaths.songs(band.id, { limit: 100, offset: 0 }), requestOptions(controller.signal)).then((response) => setSongs(response?.songs || [])).catch(() => setSongs([])); return () => controller.abort(); }, [api, band.id]);
  const change = (event) => setValue((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  const save = async (event) => {
    event.preventDefault(); if (savingRef.current || resultUnknown) return;
    const relationshipsChanged = Boolean(item) && [...(item.songs || []).map((song) => String(song.id))].sort().join(',') !== [...value.song_ids].sort().join(',');
    const decision = reviewPublicVisibility(config, { previous: item?.visibility, next: value.visibility, relationshipsChanged });
    if (!decision.proceed) { if (decision.error) setError(decision.error); return; }
    savingRef.current = true; setSaving(true); setError(''); const controller = new AbortController(); saveController.current = controller; let submitted = false;
    try {
      if (candidate.current) {
        const current = await api.requestJson(availabilityPath(band.id, value.date, value.date), { signal: controller.signal });
        if (controller.signal.aborted || !live.current) return;
        const fingerprint = availabilityFingerprint(current, value.date);
        if (value.date !== candidate.current.date || fingerprint !== candidate.current.fingerprint) {
          const changed = current.days.filter((day) => day.date === value.date); const available = changed.filter((day) => day[candidate.current.period] === 'AVAILABLE').length;
          if (!window.confirm(`가능 일정이나 날짜가 변경되었습니다. ${value.date} ${PERIOD_LABELS[candidate.current.period]} 현재 가능 ${available}명 / 활성 멤버 ${current.members.length}명입니다. 미응답은 가능에 포함하지 않습니다. 이대로 합주를 저장할까요?`)) return;
        }
      }
      const path = item ? bandWorkspacePaths.rehearsal(band.id, item.id) : bandWorkspacePaths.rehearsals(band.id);
      submitted = true;
      const rehearsalValue = item && value.rehearsal_type === originalRehearsalType.current ? { ...value, rehearsal_type: undefined } : value;
      await api.requestJson(path, { method: item ? 'PATCH' : 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...rehearsalPayload(rehearsalValue), ...(decision.confirmation ? { visibility_confirm: decision.confirmation } : {}) }) });
      if (!controller.signal.aborted && live.current) onDone();
    } catch (cause) { if (!controller.signal.aborted && live.current) {
      if (candidate.current && submitted && (!cause.status || cause.status >= 500)) { setResultUnknown(true); setError('저장 결과를 확인하지 못했습니다. 중복 생성을 막기 위해 다시 저장하지 않습니다. 취소로 폼을 닫은 뒤 합주 목록을 새로고침해 결과를 확인해 주세요.'); }
      else setError('저장하지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.');
    } }
    finally { savingRef.current = false; if (live.current) setSaving(false); }
  };
  return <form className="workspace-form" onSubmit={save}><h2>{item ? '수정' : '새로 추가'}</h2>{candidate.current && <p className="availability-notice">조율 후보: {candidate.current.date} · {PERIOD_LABELS[candidate.current.period]}. 정확한 합주 시각은 직접 입력해 주세요. 저장 직전 가능 상태를 다시 확인하지만 예약이나 참석을 보장하지는 않습니다.</p>}<label>제목<input required name="title" value={value.title} onChange={change} /></label><label>합주 유형<select name="rehearsal_type" value={value.rehearsal_type} onChange={change}>{REHEARSAL_TYPES.map(([type, text]) => <option key={type} value={type}>{type === 'UNCLASSIFIED' ? `${text} (유형 선택)` : text}</option>)}</select></label>{value.rehearsal_type === 'UNCLASSIFIED' && <p className="workspace-muted">기존 합주 유형은 추정하지 않았습니다. 실제 유형을 선택해 주세요.</p>}<label>날짜<input required type="date" name="date" value={value.date} onChange={change} /></label><label>시간<input name="time" value={value.time} onChange={change} /></label><label>장소<input name="location" value={value.location} maxLength={200} onChange={change} /></label><BandPlacePicker bandId={band.id} workspaceKey={workspaceKey} api={api} onSelect={(location) => setValue((previous) => ({ ...previous, location }))} /><fieldset><legend>연결할 곡</legend>{songs.map((song) => <label className="check-row" key={song.id}><input type="checkbox" checked={value.song_ids.includes(String(song.id))} onChange={(event) => setValue((previous) => ({ ...previous, song_ids: event.target.checked ? [...previous.song_ids, String(song.id)] : previous.song_ids.filter((id) => id !== String(song.id)) }))} />{song.title} — {song.artist}</label>)}</fieldset><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label><label>공개 범위<select name="visibility" value={value.visibility} onChange={change}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={onCancel}>취소</button><button className="primary-btn" disabled={saving || resultUnknown}>{saving ? '저장 중…' : '저장'}</button></div></form>;
};
