import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';

const base = '/api/v2/public/archive';
const validCount = (value) => Number.isSafeInteger(value) && value >= 0;

function ShareMediaButton({ item }) {
  const [message, setMessage] = useState('');
  const [manualUrl, setManualUrl] = useState('');
  const locked = useRef(false);

  async function share() {
    if (locked.current) return;
    locked.current = true;
    const url = new URL(`/archive/media/${encodeURIComponent(item.id)}`, window.location.origin).href;
    const title = item.song?.title || '공개 아카이브';
    setMessage('');
    setManualUrl('');
    try {
      if (typeof navigator.share === 'function') {
        try {
          await navigator.share({ title, url });
          return;
        } catch (error) {
          if (error?.name === 'AbortError') return;
        }
      }
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(url);
          setMessage('공개 링크를 복사했습니다.');
          return;
        } catch {
          // Some browsers allow copying only through a manual selection.
        }
      }
      setManualUrl(url);
    } finally {
      locked.current = false;
    }
  }

  return <>
    <button type="button" className="bands-return-link" onClick={share}>공유</button>
    {message && <span role="status">{message}</span>}
    {manualUrl && <input aria-label="공개 링크 복사" readOnly value={manualUrl} onFocus={(event) => event.currentTarget.select()} />}
  </>;
}

export function ArchiveMediaEngagement({ media, children }) {
  const auth = useAuth();
  const identity = auth.loading ? 'loading' : auth.user?.id ?? 'anonymous';
  return <ViewerEngagement key={`${identity}:${auth.authGeneration ?? 0}`} auth={auth} media={media}>{children}</ViewerEngagement>;
}

function ViewerEngagement({ auth, media, children }) {
  const { user, loading, getAccessToken, refreshSession } = auth;
  const api = useMemo(() => createAuthenticatedApiClient({
    getAccessToken, refreshSession, fetchImpl: (...args) => fetch(...args),
  }), [getAccessToken, refreshSession]);
  const known = useRef(new Set());
  const writes = useRef(new Map());
  const [values, setValues] = useState({});
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const ids = media.filter((item) => Number.isSafeInteger(item.id) && item.id > 0 && validCount(item.like_count))
    .map((item) => item.id).join(',');

  useEffect(() => {
    if (!user || !ids) return;
    const missing = ids.split(',').map(Number).filter((id) => !known.current.has(id));
    if (missing.length === 0) return;
    const controller = new AbortController();
    const batches = [];
    for (let i = 0; i < missing.length; i += 20) batches.push(missing.slice(i, i + 20));
    Promise.all(batches.map((batch) => api.requestJson(`${base}/likes?media_ids=${batch.join(',')}`, {
      signal: controller.signal, cache: 'no-store',
    }).then((result) => {
      if (!Array.isArray(result?.liked_media_ids)) throw new Error('Invalid archive likes response');
      if (controller.signal.aborted) return;
      const liked = new Set(result.liked_media_ids);
      for (const id of batch) known.current.add(id);
      setValues((previous) => {
        const next = { ...previous };
        for (const id of batch) if (!next[id]) next[id] = { liked: liked.has(id) };
        return next;
      });
    }))).catch(() => {
      if (!controller.signal.aborted) setError(true);
    });
    return () => controller.abort();
  }, [api, ids, retry, user]);

  useEffect(() => {
    const controllers = writes.current;
    return () => {
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
    };
  }, []);

  async function toggle(id, liked) {
    if (!user || writes.current.has(id)) return;
    const controller = new AbortController();
    writes.current.set(id, controller);
    setValues((previous) => ({ ...previous, [id]: { ...previous[id], pending: true, error: false } }));
    try {
      const result = await api.requestJson(`${base}/media/${id}/like`, {
        method: 'PUT', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ liked }),
      });
      if (typeof result?.liked !== 'boolean' || !validCount(result.like_count)) throw new Error('Invalid archive like');
      if (!controller.signal.aborted) {
        known.current.add(id);
        setValues((previous) => ({ ...previous, [id]: { liked: result.liked, count: result.like_count } }));
      }
    } catch {
      if (!controller.signal.aborted) setValues((previous) => ({
        ...previous, [id]: { ...previous[id], pending: false, error: true },
      }));
    } finally {
      if (writes.current.get(id) === controller) writes.current.delete(id);
    }
  }

  return <>
    {error && <div className="archive-playback-actions">
      <span role="status">내 좋아요를 불러오지 못했습니다.</span>
      <button type="button" className="bands-return-link" onClick={() => {
        setError(false); setRetry((value) => value + 1);
      }}>좋아요 다시 확인</button>
    </div>}
    {children((item) => {
      const value = values[item.id];
      const count = value?.count ?? item.like_count;
      return <div className="archive-playback-actions">
        {validCount(count) && (user || loading
          ? <button type="button" className="bands-return-link" disabled={!value || value.pending}
              aria-pressed={value ? value.liked : undefined} onClick={() => toggle(item.id, !value.liked)}>
              {value?.liked ? '좋아요 취소' : '좋아요'} <span>{count}</span>
            </button>
          : <Link className="bands-return-link" to={`/login?returnTo=${encodeURIComponent(`/archive/media/${item.id}`)}`}
              title="로그인 후 좋아요를 누를 수 있어요">좋아요 {count}<span>· 로그인</span></Link>)}
        <ShareMediaButton item={item} />
        {value?.error && <span role="alert">좋아요를 저장하지 못했습니다.</span>}
      </div>;
    })}
  </>;
}
