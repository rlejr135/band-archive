/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useRef, useState } from 'react';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { requestOptions } from '../workspaceReadSupport.js';

export const PersonalLogPlayback = ({ log, workspaceKey, onRefresh, onDeleted }) => {
  const { band, api } = useWorkspace();
  const [processing, setProcessing] = useState(null);
  const [processingError, setProcessingError] = useState(false);
  const staleAttempt = useRef(false);
  useEffect(() => {
    const controller = new AbortController(); let live = true;
    setProcessing(null); setProcessingError(false);
    api.requestJson(bandWorkspacePaths.personalLogProcessing(band.id, log.id), requestOptions(controller.signal)).then((response) => {
      if (live && !controller.signal.aborted) setProcessing(response?.processing ?? null);
    }).catch(() => { if (live && !controller.signal.aborted) setProcessingError(true); });
    return () => { live = false; controller.abort(); };
  }, [api, band.id, log.id, workspaceKey]);
  const source = log.file_type === 'video' ? (log.playback?.video_url || log.playback?.url) : (log.playback?.audio_url || log.playback?.url);
  const refreshExpiredUrl = () => { if (!staleAttempt.current) { staleAttempt.current = true; onRefresh(); } };
  return <article className="workspace-media"><div className="detail-heading"><h3>{log.title || '개인 연습 기록'}</h3><BandDeleteAction band={band} workspaceKey={`${workspaceKey}:log:${log.id}`} canDelete={log.can_delete} label="개인 연습 기록" path={bandWorkspacePaths.personalLog(band.id, log.id)} asynchronous resourceType="personal_log" onDeleted={onDeleted} /></div>{source ? log.file_type === 'video' ? <video controls preload="metadata" src={source} onError={refreshExpiredUrl}>브라우저가 영상을 지원하지 않습니다.</video> : <audio controls preload="metadata" src={source} onError={refreshExpiredUrl}>브라우저가 오디오를 지원하지 않습니다.</audio> : <p className="workspace-muted">현재 재생 가능한 파일이 없습니다.</p>}
    {processing?.status && <p className="workspace-muted">처리 상태: {processing.status === 'failed' ? '처리에 실패했습니다.' : processing.status}</p>}{processingError && <p className="workspace-muted">처리 상태를 확인하지 못했습니다.</p>}
  </article>;
};
