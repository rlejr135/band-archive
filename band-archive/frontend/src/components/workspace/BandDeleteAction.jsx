import { useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { bandWorkspacePaths } from '../../services/bandWorkspaceApi.js';
import { DeletionTaskError, deletionErrorMessage, pollDeletionTask } from '../../services/deletionTasks.js';

const taskLabel = (value) => ({ queued: '대기 중', pending: '대기 중', running: '처리 중', processing: '처리 중', retry_wait: '재시도 대기 중' }[String(value || '').toLowerCase()] || '확인 중');

export const BandDeleteAction = ({ band, workspaceKey, canDelete, label, path, asynchronous = false, resourceType, onDeleted, className = 'danger-btn' }) => {
  const { user, getAccessToken, refreshSession } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const scope = `${user?.id || 'anonymous'}:${band?.id}:${workspaceKey || ''}:${path}:${asynchronous}:${canDelete === true}`;
  const [state, setState] = useState({ busy: false, message: '', denied: false });
  const controllerRef = useRef(null); const flightRef = useRef(false); const scopeRef = useRef(scope); scopeRef.current = scope;
  useEffect(() => {
    setState({ busy: false, message: '', denied: false }); flightRef.current = false;
    return () => { controllerRef.current?.abort(); controllerRef.current = null; flightRef.current = false; };
  }, [scope]);
  if (canDelete !== true) return null;
  const remove = async () => {
    if (flightRef.current || !window.confirm(`“${label}”을(를) 삭제할까요? 삭제 후에는 복구할 수 없습니다. 밴드 owner 또는 권한 있는 superadmin 작업입니다.`)) return;
    flightRef.current = true; const requestedScope = scope; const controller = new AbortController(); controllerRef.current?.abort(); controllerRef.current = controller;
    const current = () => !controller.signal.aborted && scopeRef.current === requestedScope;
    let timedOut = false; const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 70000);
    setState({ busy: true, message: '', denied: false });
    try {
      const result = await api.requestJson(path, { method: 'DELETE', signal: controller.signal });
      if (!current()) return;
      if (asynchronous && String(result?.status || '').toLowerCase() !== 'completed') {
        const taskStatus = String(result?.status || '').toLowerCase();
        if (!result?.task_id || !['queued', 'pending', 'running', 'processing', 'retry_wait'].includes(taskStatus)) throw new Error('invalid_task_response');
        setState({ busy: true, message: '삭제 작업을 안전하게 처리하고 있습니다…', denied: false });
        await pollDeletionTask({ api, path: (taskId) => bandWorkspacePaths.deletionTask(band.id, taskId), taskId: result.task_id, signal: controller.signal, retryAfterSeconds: result.retry_after_seconds, expectedResourceType: resourceType, expectedResourceId: path.split('/').at(-1), onStatus: (task) => { if (current()) setState({ busy: true, message: `삭제 작업 ${taskLabel(task?.status)}…`, denied: false }); } });
      }
      if (!current()) return;
      setState({ busy: false, message: '', denied: false }); onDeleted?.();
    } catch (error) {
      if (current()) setState({ busy: false, message: deletionErrorMessage(error), denied: error?.status === 401 || error?.status === 403 });
      else if (timedOut && scopeRef.current === requestedScope) setState({ busy: false, message: deletionErrorMessage(new DeletionTaskError('timeout')), denied: false });
    } finally { window.clearTimeout(timeout); if (scopeRef.current === requestedScope && controllerRef.current === controller) flightRef.current = false; }
  };
  return <span className="band-delete-action"><button type="button" className={className} disabled={state.busy || state.denied} onClick={remove}>{state.busy ? '삭제 처리 중…' : state.denied ? '권한 확인 필요' : '삭제'}</button>{state.message && <span role={state.busy ? 'status' : 'alert'} aria-live="polite">{state.message}</span>}</span>;
};
