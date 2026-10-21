import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useServicePolicy } from '../../context/ServicePolicyContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { uploadPolicyConfirmation, uploadPolicyMode } from '../../services/uploadPolicy.js';
import { PolicyChecks } from '../auth/PolicyPages.jsx';

export const UploadReview = (props) => {
  const { user, authGeneration = 0 } = useAuth();
  const { config } = useServicePolicy();
  const identity = [
    user?.id || 'anonymous', authGeneration, props.band?.id || '',
    props.files.map((entry) => entry.id).join(','), props.parentVisibility || '',
    props.band?.default_visibility || '', config?.policies?.ready,
    config?.policies?.terms_version, config?.policies?.privacy_version,
    config?.policies?.upload?.version, config?.policies?.upload?.enabled,
  ].join(':');
  return <UploadReviewForm key={identity} {...props} />;
};

const UploadReviewForm = ({ files, band, parentVisibility, targetLabel, onExclude, onCancel, onStart }) => {
  const { getAccessToken, refreshSession } = useAuth();
  const { config, loading, error, retry } = useServicePolicy();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const mode = uploadPolicyMode(config);
  const [status, setStatus] = useState(mode === 'enabled' ? 'loading' : mode === 'disabled' ? 'accepted' : 'unavailable');
  const [terms, setTerms] = useState(false);
  const [age, setAge] = useState(false);
  const [notice, setNotice] = useState(false);
  const [rights, setRights] = useState(false);
  const [visibility, setVisibility] = useState('MEMBERS');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  const controller = useRef(null);
  const flight = useRef(false);
  const current = useRef(true);

  useEffect(() => {
    current.current = true;
    if (mode !== 'enabled') {
      return () => { current.current = false; controller.current?.abort(); };
    }
    const request = new AbortController();
    controller.current = request;
    api.requestJson('/api/v2/me', { signal: request.signal }).then((data) => {
      if (request.signal.aborted || !current.current) return;
      const required = data?.user?.upload_policy_acceptance_required;
      if (typeof required !== 'boolean') throw new Error('invalid_upload_policy_status');
      setStatus(required ? 'required' : 'accepted');
    }).catch(() => {
      if (!request.signal.aborted && current.current) setStatus('error');
    });
    return () => {
      current.current = false;
      request.abort();
      controller.current?.abort();
    };
  }, [api, mode]);

  const canStart = files.length > 0 && mode !== 'unavailable' && (
    mode === 'disabled' || (
      notice && rights && ['accepted', 'required'].includes(status)
      && (status !== 'required' || (terms && age))
    )
  );

  const start = async (event) => {
    event.preventDefault();
    if (flight.current || !current.current || !canStart) return;
    flight.current = true;
    setBusy(true);
    setFailure('');
    const request = new AbortController();
    controller.current = request;
    try {
      if (mode === 'enabled' && status === 'required') {
        const receipt = await api.requestJson('/api/v2/me/upload-policy-acceptance', {
          method: 'POST', signal: request.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            terms_version: config.policies.terms_version,
            privacy_version: config.policies.privacy_version,
            terms_accepted: true, age_14_plus: true,
          }),
        });
        if (receipt?.accepted !== true) throw new Error('invalid_upload_receipt');
      }
      if (!current.current || request.signal.aborted) return;
      const uploadPolicy = mode === 'enabled'
        ? uploadPolicyConfirmation(config, globalThis.crypto.randomUUID(), visibility)
        : undefined;
      await onStart({ uploadPolicy });
    } catch {
      if (current.current && !request.signal.aborted) {
        setFailure('업로드를 시작하지 못했습니다. 현재 안내와 권한을 확인한 뒤 다시 시도해 주세요.');
      }
    } finally {
      if (current.current && !request.signal.aborted) {
        flight.current = false;
        setBusy(false);
      }
    }
  };

  const limitedByParent = parentVisibility === 'MEMBERS' || band?.default_visibility === 'MEMBERS';
  const effectiveMembers = visibility === 'MEMBERS' || limitedByParent;
  const cancel = () => {
    controller.current?.abort();
    onCancel();
  };

  return <section className="upload-review" aria-label="업로드 전 확인">
    <h3>업로드 전 확인</h3>
    <p>{band?.name || '선택한 밴드'} · {targetLabel || '미디어'} · {files.length}개 파일</p>
    <ul>{files.map((entry) => <li key={entry.id}>
      <span>{entry.file.name}</span>
      <button className="secondary-btn" type="button" disabled={busy} onClick={() => onExclude(entry.id)} aria-label={`${entry.file.name} 제외`}>제외</button>
    </li>)}</ul>
    <form onSubmit={start} aria-busy={busy}>
      {mode === 'enabled' ? <>
        <p>선택한 파일은 저장·백업되며, 영상은 재생용 변환이나 M4A 음원 추출 처리가 이루어질 수 있습니다. 밴드를 탈퇴해도 공동 콘텐츠는 자동 삭제되지 않습니다. <Link to="/upload-policy" target="_blank" rel="noopener noreferrer">업로드 안내·권리 요청</Link></p>
        <fieldset className="upload-review-policy" disabled={busy}>
          <label>공개 범위<select value={visibility} onChange={(event) => { setVisibility(event.target.value); setRights(false); }}>
            <option value="MEMBERS">멤버 전용</option><option value="PUBLIC">전체 공개</option>
          </select></label>
          <p className="upload-review-help">{effectiveMembers ? '유효 공개 범위: 밴드 멤버 전용' : '유효 공개 범위: 전체 공개 — 비회원도 볼 수 있습니다.'}
            {visibility === 'PUBLIC' && limitedByParent ? ' · 부모 콘텐츠나 밴드가 멤버 전용이므로 지금은 외부에 공개되지 않습니다. 상위 공개 범위가 확대되면 보일 수 있습니다.' : ''}
          </p>
          {status === 'loading' && <p role="status">최초 업로드 확인 상태를 불러오는 중입니다…</p>}
          {status === 'error' && <p role="alert">확인 상태를 불러오지 못했습니다. 취소 후 다시 파일을 선택해 주세요.</p>}
          {status === 'required' && <>
            <h4>최초 업로드 확인</h4>
            <p>기존 계정도 현재 이용약관과 연령 기준을 확인해 주세요. 개인정보처리방침 열람은 포괄 동의가 아닙니다.</p>
            <PolicyChecks terms={terms} age={age} onTerms={setTerms} onAge={setAge} />
          </>}
          <label className="upload-review-check"><input type="checkbox" checked={notice} onChange={(event) => setNotice(event.target.checked)} />자동 처리·공개 범위·탈퇴 후 콘텐츠 보존·문의 방법 안내를 확인했습니다.</label>
          <label className="upload-review-check"><input type="checkbox" checked={rights} onChange={(event) => setRights(event.target.checked)} />선택한 파일을 이 범위로 게시·공유할 권한이 있고 필요한 출연자·권리자의 허락을 받았습니다.</label>
        </fieldset>
      </> : mode === 'unavailable' ? <>
        <p role="status">{loading ? '서비스 설정을 불러오는 중입니다…' : error || '업로드 설정을 확인하지 못했습니다.'}</p>
        {!loading && <button type="button" className="secondary-btn" onClick={retry}>설정 다시 확인</button>}
      </> : <p>파일을 확인한 뒤 업로드 시작을 눌러 주세요.</p>}
      {failure && <p role="alert" className="progress-error">{failure}</p>}
      <div className="band-actions">
        <button type="button" className="secondary-btn" onClick={cancel}>선택 취소</button>
        <button type="submit" className="primary-btn" disabled={busy || !canStart}>{busy ? '확인 중…' : '업로드 시작'}</button>
      </div>
    </form>
  </section>;
};
