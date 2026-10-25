import { useEffect, useRef, useState } from 'react';
import { useServicePolicy } from '../../../context/ServicePolicyContext.jsx';
import { reviewPublicVisibility } from '../../../services/uploadPolicy.js';
import { bandWorkspacePaths, songPayload } from '../../../services/bandWorkspaceApi.js';
import { useWorkspace } from '../WorkspaceContext.jsx';

export const SongForm = ({ item, onDone, onCancel }) => {
  const { config } = useServicePolicy();
  const { band, api } = useWorkspace();
  const [saving, setSaving] = useState(false); const savingRef = useRef(false); const [error, setError] = useState('');
  const saveController = useRef(null); const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; saveController.current?.abort(); }; }, []);
  const initial = { title: item?.title || '', artist: item?.artist || '', status: item?.status || 'Practice', chords: item?.chords || '', link: item?.link || '', memo: item?.memo || '', genre: item?.genre || '', difficulty: item?.difficulty || '', visibility: item?.visibility || band.default_visibility || 'MEMBERS' };
  const [value, setValue] = useState(initial);
  const change = (event) => setValue((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  const save = async (event) => {
    event.preventDefault(); if (savingRef.current) return;
    const decision = reviewPublicVisibility(config, { previous: item?.visibility, next: value.visibility, relationshipsChanged: false });
    if (!decision.proceed) { if (decision.error) setError(decision.error); return; }
    savingRef.current = true; setSaving(true); setError(''); const controller = new AbortController(); saveController.current = controller;
    try {
      const path = item ? bandWorkspacePaths.song(band.id, item.id) : bandWorkspacePaths.songs(band.id);
      await api.requestJson(path, { method: item ? 'PATCH' : 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...songPayload(value), ...(decision.confirmation ? { visibility_confirm: decision.confirmation } : {}) }) });
      if (!controller.signal.aborted && live.current) onDone();
    } catch { if (!controller.signal.aborted && live.current) setError('저장하지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.'); }
    finally { savingRef.current = false; if (live.current) setSaving(false); }
  };
  return <form className="workspace-form" onSubmit={save}><h2>{item ? '수정' : '새로 추가'}</h2><label>제목<input required name="title" value={value.title} onChange={change} /></label><label>아티스트<input required name="artist" value={value.artist} onChange={change} /></label><label>상태<select name="status" value={value.status} onChange={change}><option>Practice</option><option>Completed</option><option>OnHold</option></select></label><label>장르<input name="genre" value={value.genre} onChange={change} /></label><label>메모<textarea name="memo" value={value.memo} onChange={change} /></label><label>공개 범위<select name="visibility" value={value.visibility} onChange={change}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={onCancel}>취소</button><button className="primary-btn" disabled={saving}>{saving ? '저장 중…' : '저장'}</button></div></form>;
};
