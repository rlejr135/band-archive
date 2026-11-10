/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useRef, useState } from 'react';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { workspaceMediaFile } from '../../../services/playbackMedia.js';
import MediaPlayer from '../../common/MediaPlayer.jsx';
import { BandDeleteAction } from '../BandDeleteAction.jsx';
import { useWorkspace } from '../WorkspaceContext.jsx';
import { requestOptions } from '../workspaceReadSupport.js';

export const PersonalLogPlayback = ({ log, workspaceKey, onRefresh, onDeleted }) => {
  const { band, api } = useWorkspace();
  return <PersonalLogPlaybackContent key={`${workspaceKey}:band:${band.id}:log:${log.id}`} log={log} band={band} api={api} workspaceKey={workspaceKey} onRefresh={onRefresh} onDeleted={onDeleted} />;
};

const PersonalLogPlaybackContent = ({ log, band, api, workspaceKey, onRefresh, onDeleted }) => {
  const [processing, setProcessing] = useState(null);
  const [processingError, setProcessingError] = useState(false);
  const staleAttempt = useRef(false);
  const initialSources = useRef({ video: log.file_type === 'video', audio: Boolean(log.playback?.audio_url) });
  const refreshRef = useRef(onRefresh);
  useEffect(() => { refreshRef.current = onRefresh; }, [onRefresh]);
  useEffect(() => {
    const controller = new AbortController(); let live = true;
    setProcessing(null); setProcessingError(false);
    api.requestJson(bandWorkspacePaths.personalLogProcessing(band.id, log.id), requestOptions(controller.signal)).then((response) => {
      if (!live || controller.signal.aborted) return;
      const next = response?.processing ?? null;
      setProcessing(next);
      // Status reads do not contain signed playback URLs. Renew the selected
      // page once when extraction completed while its audio source was absent.
      if (next?.status === 'completed' && initialSources.current.video && !initialSources.current.audio) refreshRef.current?.();
    }).catch(() => { if (live && !controller.signal.aborted) setProcessingError(true); });
    return () => { live = false; controller.abort(); };
  }, [api, band.id, log.id, workspaceKey]);
  const refreshExpiredUrl = () => { if (!staleAttempt.current) { staleAttempt.current = true; onRefresh(); } };
  return <article className="workspace-media"><div className="detail-heading"><h3>{log.title || '개인 연습 기록'}</h3><BandDeleteAction band={band} workspaceKey={`${workspaceKey}:log:${log.id}`} canDelete={log.can_delete} label="개인 연습 기록" path={bandWorkspacePaths.personalLog(band.id, log.id)} asynchronous resourceType="personal_log" onDeleted={onDeleted} /></div><MediaPlayer file={workspaceMediaFile(log, processing)} playbackIdentity={`${workspaceKey}:band:${band.id}:personal-log:${log.id}`} showTitle={false} onPlaybackError={refreshExpiredUrl} onRefresh={onRefresh} />
    {processing?.status && <p className="workspace-muted">처리 상태: {processing.status === 'failed' ? '처리에 실패했습니다.' : processing.status}</p>}{processingError && <p className="workspace-muted">처리 상태를 확인하지 못했습니다.</p>}
  </article>;
};
