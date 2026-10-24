import { useEffect, useRef, useState } from 'react';
import { useServicePolicy } from '../../../context/ServicePolicyContext.jsx';
import { reviewPublicVisibility } from '../../../services/uploadPolicy.js';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import '../BandWorkspace.css';

export const AnnouncementEditor = ({ announcement, band, api, workspaceKey, onSaved, onForbidden }) => {
  const { config } = useServicePolicy();
  const [editing, setEditing] = useState(false); const [content, setContent] = useState(''); const [visibility, setVisibility] = useState('MEMBERS'); const [saving, setSaving] = useState(false); const [error, setError] = useState('');
  const savingRef = useRef(false); const generation = useRef(0); const controllerRef = useRef(null);
  const defaultVisibility = announcement?.visibility === 'PUBLIC' || announcement?.visibility === 'MEMBERS' ? announcement.visibility : band.default_visibility === 'PUBLIC' ? 'PUBLIC' : 'MEMBERS';
  useEffect(() => {
    generation.current += 1;
    controllerRef.current?.abort(); controllerRef.current = null;
    savingRef.current = false;
    setEditing(false); setContent(announcement?.content || ''); setVisibility(defaultVisibility); setSaving(false); setError('');
    return () => { generation.current += 1; controllerRef.current?.abort(); controllerRef.current = null; savingRef.current = false; };
  }, [announcement?.content, announcement?.visibility, defaultVisibility, workspaceKey]);
  const save = async (event) => {
    event.preventDefault(); if (savingRef.current || !content.trim()) return;
    const decision = reviewPublicVisibility(config, { previous: announcement?.visibility, next: visibility });
    if (!decision.proceed) { if (decision.error) setError(decision.error); return; }
    const token = ++generation.current; const controller = new AbortController();
    controllerRef.current?.abort(); controllerRef.current = controller;
    savingRef.current = true; setSaving(true); setError('');
    try {
      const response = await api.requestJson(bandWorkspacePaths.announcement(band.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: content.trim(), visibility, ...(decision.confirmation ? { visibility_confirm: decision.confirmation } : {}) }), signal: controller.signal });
      if (controller.signal.aborted || token !== generation.current) return;
      if (response?.announcement) onSaved?.(response.announcement);
      setEditing(false);
    } catch (saveError) {
      if (controller.signal.aborted || token !== generation.current) return;
      if (saveError?.status === 403) onForbidden?.();
      else setError('공지사항을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      if (!controller.signal.aborted && token === generation.current) { savingRef.current = false; controllerRef.current = null; setSaving(false); }
    }
  };
  const canManage = announcement ? announcement.can_manage : band.can_manage;
  return <section className="workspace-announcement" aria-label="공지사항"><div className="detail-heading"><h2>공지사항</h2>{canManage && !editing && <button type="button" className="secondary-btn" onClick={() => setEditing(true)}>{announcement ? '공지 수정' : '공지 작성'}</button>}<BandDeleteAction band={band} workspaceKey={`${workspaceKey}:announcement`} canDelete={Boolean(announcement?.content) && announcement?.can_delete} label="공지사항" path={bandWorkspacePaths.announcement(band.id)} onDeleted={() => onSaved?.({ ...announcement, content: '', visibility: defaultVisibility })} /></div>
    {editing ? <form className="workspace-form" onSubmit={save}><label>공지 내용<textarea aria-label="공지 내용" maxLength={10000} value={content} onChange={(event) => setContent(event.target.value)} /></label><label>공개 범위<select aria-label="공지 공개 범위" value={visibility} onChange={(event) => setVisibility(event.target.value)}><option value="MEMBERS">멤버 전용</option><option value="PUBLIC">공개</option></select></label>{error && <p className="workspace-error" role="alert">{error}</p>}<div className="form-actions"><button type="button" className="secondary-btn" disabled={saving} onClick={() => { setEditing(false); setContent(announcement?.content || ''); setVisibility(defaultVisibility); }}>취소</button><button className="primary-btn" disabled={saving || !content.trim()}>{saving ? '저장 중…' : '저장'}</button></div></form> : announcement?.content ? <p>{announcement.content}</p> : <p className="workspace-muted">현재 공개된 공지사항이 없습니다.</p>}
  </section>;
};
