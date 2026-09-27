import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { useBands } from '../../context/BandContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import './BandsPage.css';

export const BandsPage = () => {
  const { user, getAccessToken, refreshSession } = useAuth();
  const { activeBands, selectedBand, setAvailableBands, selectBand } = useBands();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pendingMemberships, setPendingMemberships] = useState([]);
  const [publicBands, setPublicBands] = useState([]);
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);

  useEffect(() => {
    let current = true;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        // Read the local account before bands so future server-side profile
        // fields remain available without relying on the browser email claim.
        const [me, bandsResponse, publicResponse] = await Promise.all([
          api.requestJson('/api/v2/me'),
          api.requestJson('/api/v2/bands'),
          api.requestJson('/api/v2/public/bands', { authenticated: false }),
        ]);
        if (!current) return;
        setAvailableBands(bandsResponse?.bands);
        // The current server contract returns active memberships in `bands`.
        // Accept a future explicit pending list without inferring it client-side.
        setPendingMemberships(Array.isArray(bandsResponse?.pending_memberships) ? bandsResponse.pending_memberships : []);
        setPublicBands(Array.isArray(publicResponse?.bands) ? publicResponse.bands : []);
        if (!me?.user) setError('계정 정보를 확인하지 못했습니다. 다시 시도해 주세요.');
      } catch {
        if (current) setError('밴드 정보를 불러오지 못했습니다. 다시 시도해 주세요.');
      } finally {
        if (current) setLoading(false);
      }
    };
    load();
    return () => { current = false; };
  }, [api, setAvailableBands, user?.id]);

  return (
    <section className="bands-page" aria-labelledby="bands-title">
      <div>
        <h2 id="bands-title">내 밴드</h2>
        <p className="bands-help">활성 멤버십에서 작업할 밴드를 선택하세요.</p>
      </div>
      {loading ? <p>밴드 정보를 불러오는 중입니다…</p> : error ? <p className="auth-error" role="alert">{error}</p> : <>
        <section aria-labelledby="active-bands-title">
          <h3 id="active-bands-title">활성 멤버십</h3>
          {activeBands.length === 0 ? <div className="bands-empty"><p>현재 활성화된 밴드가 없습니다.</p><button type="button" className="secondary-btn" disabled>공개 밴드 찾기 (준비 중)</button><button type="button" className="secondary-btn" disabled>밴드 생성 요청 (준비 중)</button></div> : <ul className="band-list">
            {activeBands.map((band) => <li key={band.id}>
              <button type="button" className={selectedBand?.id === band.id ? 'band-choice selected' : 'band-choice'} onClick={() => selectBand(band.id)} aria-pressed={selectedBand?.id === band.id}>
                <strong>{band.name}</strong><span>{band.membership?.role || 'member'}</span>
              </button><button type="button" className="secondary-btn" onClick={() => navigate(`/bands/${encodeURIComponent(band.slug)}`)}>공간 열기</button>
            </li>)}
          </ul>}
        </section>
        <section aria-labelledby="public-bands-title"><h3 id="public-bands-title">공개 밴드</h3>
          {publicBands.filter((band) => !activeBands.some((active) => active.id === band.id)).length === 0 ? <p className="bands-help">표시할 공개 밴드가 없습니다.</p> : <ul className="band-list">{publicBands.filter((band) => !activeBands.some((active) => active.id === band.id)).map((band) => <li key={band.id}><div><strong>{band.name}</strong><p className="bands-help">{band.description || '공개된 곡과 합주를 둘러볼 수 있습니다.'}</p></div><button type="button" className="secondary-btn" onClick={() => navigate(`/bands/${encodeURIComponent(band.slug)}`)}>읽기 전용으로 열기</button><button type="button" className="secondary-btn" disabled title="가입 요청 기능은 다음 API 단계에서 제공됩니다.">가입 요청 준비 중</button></li>)}</ul>}
        </section>
        <section aria-labelledby="pending-bands-title">
          <h3 id="pending-bands-title">가입 대기</h3>
          {pendingMemberships.length === 0 ? <p className="bands-help">가입 대기 중인 밴드가 없습니다.</p> : <ul className="band-list">{pendingMemberships.map((membership) => <li key={membership.id}>{membership.band?.name || '밴드'} — 승인 대기</li>)}</ul>}
        </section>
      </>}
    </section>
  );
};
