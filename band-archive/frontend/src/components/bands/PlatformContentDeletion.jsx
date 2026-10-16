import { useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import { DeletionTaskError, deletionErrorMessage, pollDeletionTask } from '../../services/deletionTasks.js';

const resources = [
  ['media', '미디어'], ['personal_log', '개인 연습 기록'], ['gallery', '갤러리 사진'], ['song', '곡'],
  ['rehearsal', '합주'], ['member', '멤버'], ['comment', '댓글'], ['suggestion', '추천글'], ['announcement', '공지사항'],
];
const taskLabel = (status) => ({ queued: '대기 중', pending: '대기 중', running: '처리 중', processing: '처리 중', retry_wait: '재시도 대기 중' }[String(status || '').toLowerCase()] || '확인 중');
const positiveInteger = (value) => /^\d+$/.test(String(value)) && Number.isSafeInteger(Number(value)) && Number(value) > 0;

export const PlatformContentDeletion = ({ allowed }) => {
  const auth = useAuth();
  return <PlatformContentDeletionForm key={`${auth.user?.id || 'anonymous'}:${allowed}`} allowed={allowed} {...auth} />;
};

const PlatformContentDeletionForm = ({ allowed, user, getAccessToken, refreshSession }) => {
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const identity = user?.id || '';
  const [form, setForm] = useState({ bandId: '', type: 'media', resourceId: '', reason: '', phrase: '' });
  const [state, setState] = useState({ busy: false, denied: false, message: '', error: '' });
  const controllerRef = useRef(null); const flightRef = useRef(false); const identityRef = useRef(identity); identityRef.current = identity; const epoch = useRef(0);
  useEffect(() => () => { epoch.current += 1; controllerRef.current?.abort(); controllerRef.current = null; flightRef.current = false; }, [identity, allowed]);
  if (!allowed) return null;
  const selectedLabel = resources.find(([value]) => value === form.type)?.[1] || '콘텐츠';
  const phrase = positiveInteger(form.bandId) && positiveInteger(form.resourceId) ? `DELETE ${form.bandId}/${form.type}/${form.resourceId}` : '';
  const change = (event) => setForm((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  const submit = async (event) => {
    event.preventDefault();
    if (flightRef.current || state.denied) return;
    const bandId = Number(form.bandId); const resourceId = Number(form.resourceId); const reason = form.reason.trim();
    if (!positiveInteger(form.bandId) || !positiveInteger(form.resourceId) || !reason || reason.length > 500 || form.phrase !== phrase) return;
    const confirmation = `플랫폼 삭제 요청\n밴드 ID: ${bandId}\n대상 유형: ${form.type}\n대상 ID: ${resourceId}\n사유: ${reason}\n\n확인한 대상만 삭제합니다. 계속하시겠습니까?`;
    if (!window.confirm(confirmation)) return;
    flightRef.current = true; const requestEpoch = ++epoch.current; const requestedIdentity = identity; const controller = new AbortController(); controllerRef.current?.abort(); controllerRef.current = controller;
    const current = () => !controller.signal.aborted && identityRef.current === requestedIdentity && epoch.current === requestEpoch;
    let timedOut = false; const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 70000);
    setState({ busy: true, denied: false, message: '삭제 요청을 전송 중입니다…', error: '' });
    try {
      const result = await api.requestJson(bandWorkspacePaths.platformDelete(bandId), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ resource_type: form.type, resource_id: resourceId, reason, confirm: true }), signal: controller.signal });
      if (!current()) return;
      if (String(result?.status || '').toLowerCase() === 'completed') {
        if ((result?.resource_type && result.resource_type !== form.type) || (result?.resource_id != null && Number(result.resource_id) !== resourceId)) throw new Error('task_mismatch');
        setState({ busy: false, denied: false, message: '삭제 요청이 완료되었습니다.', error: '' }); return;
      }
      const taskStatus = String(result?.status || '').toLowerCase();
      if (!result?.task_id || !['queued', 'pending', 'running', 'processing', 'retry_wait'].includes(taskStatus)) throw new Error('invalid_task_response');
      setState({ busy: true, denied: false, message: '삭제 작업을 안전하게 처리하고 있습니다…', error: '' });
      await pollDeletionTask({ api, path: bandWorkspacePaths.platformDeletionTask, taskId: result.task_id, signal: controller.signal, retryAfterSeconds: result.retry_after_seconds, expectedResourceType: form.type, expectedResourceId: resourceId, onStatus: (task) => { if (current()) setState({ busy: true, denied: false, message: `삭제 작업 ${taskLabel(task?.status)}…`, error: '' }); } });
      if (current()) setState({ busy: false, denied: false, message: '삭제 작업이 완료되었습니다.', error: '' });
    } catch (error) {
      if (current()) setState({ busy: false, denied: error?.status === 401 || error?.status === 403, message: '', error: deletionErrorMessage(error) });
      else if (timedOut && identityRef.current === requestedIdentity && epoch.current === requestEpoch) setState({ busy: false, denied: false, message: '', error: deletionErrorMessage(new DeletionTaskError('timeout')) });
    } finally { window.clearTimeout(timeout); if (identityRef.current === requestedIdentity && epoch.current === requestEpoch && controllerRef.current === controller) flightRef.current = false; }
  };
  return <section className="platform-delete-panel" aria-labelledby="platform-delete-title">
    <h2 id="platform-delete-title">대상 지정 콘텐츠 삭제</h2>
    <p>대상 ID를 직접 지정합니다. 이 화면은 밴드·콘텐츠 목록이나 파일 URL을 조회하지 않습니다. 사유와 이중 확인이 필요합니다.</p>
    {state.denied ? <p className="platform-delete-error" role="alert">{state.error} 이 화면의 삭제 작업을 중지했습니다.</p> : <form onSubmit={submit}>
        <label>밴드 ID<input name="bandId" inputMode="numeric" value={form.bandId} onChange={change} disabled={state.busy} required /></label>
      <label>대상 종류<select name="type" value={form.type} onChange={change} disabled={state.busy}>{resources.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>대상 ID<input name="resourceId" inputMode="numeric" value={form.resourceId} onChange={change} disabled={state.busy} required /></label>
      <label>삭제 사유<textarea name="reason" maxLength={500} value={form.reason} onChange={change} disabled={state.busy} required /></label>
      <label>확인 문구 <code>{phrase || 'DELETE 밴드ID/종류/대상ID'}</code><input name="phrase" autoComplete="off" value={form.phrase} onChange={change} disabled={state.busy} required /></label>
      <button type="submit" className="platform-delete-submit" disabled={state.busy || form.phrase !== phrase || !phrase || !form.reason.trim()}>대상 확인 후 삭제 요청</button>
    </form>}
    {state.message && <p className="platform-delete-status" role="status" aria-live="polite">{state.message}</p>}
    {state.error && !state.denied && <p className="platform-delete-error" role="alert">{state.error}</p>}
    <small>선택한 대상: 밴드 {form.bandId || '—'} · {selectedLabel} · ID {form.resourceId || '—'}</small>
  </section>;
};
