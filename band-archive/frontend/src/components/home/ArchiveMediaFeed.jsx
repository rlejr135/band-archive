import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { safeExternalHttpUrl } from '../../services/safeExternalUrl.js';

const PAGE_SIZE = 4;
const initialFeed = { media: [], started: false, hasMore: true, pending: true, error: false };

function MediaPreview({ item, loadPreview }) {
  const box = useRef(null);
  const [frame, setFrame] = useState(null);
  const [ready, setReady] = useState(false);
  const [posterFailed, setPosterFailed] = useState(false);
  const video = item.file_type === 'video';
  const poster = video && !posterFailed ? safeExternalHttpUrl(item.poster_url) : null;

  useEffect(() => {
    if (!video || poster || !loadPreview || !box.current || typeof IntersectionObserver !== 'function') return;
    let request = null;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry?.isIntersecting) {
        request?.abort();
        request = null;
        setFrame(null);
        setReady(false);
        return;
      }
      if (request) return;
      const controller = new AbortController();
      request = controller;
      loadPreview(item.id, controller.signal).then((value) => {
        if (!controller.signal.aborted) setFrame(safeExternalHttpUrl(value));
      }).catch(() => {
        // The video artwork remains visible if a frame cannot be loaded.
      });
    });
    observer.observe(box.current);
    return () => {
      request?.abort();
      observer.disconnect();
    };
  }, [item.id, loadPreview, poster, video]);

  return <div ref={box} className={`archive-card-preview ${video ? 'archive-card-preview--video' : 'archive-card-preview--audio'}`} aria-hidden="true">
    <span className="archive-card-preview-art">{video ? '▶' : '📻'}</span>
    {poster && <img src={poster} alt="" loading="lazy" onError={() => setPosterFailed(true)} />}
    {!poster && frame && <video
      className={ready ? 'archive-card-frame is-ready' : 'archive-card-frame'}
      src={frame}
      muted
      playsInline
      preload="metadata"
      onLoadedMetadata={(event) => {
        const element = event.currentTarget;
        if (Number.isFinite(element.duration) && element.duration > 0.1) element.currentTime = 0.1;
      }}
      onLoadedData={() => setReady(true)}
      onError={() => { setFrame(null); setReady(false); }}
    />}
  </div>;
}

export function ArchiveMediaCards({ media, loadPreview }) {
  if (!media.length) return <p>표시할 공개 영상·음원이 없습니다.</p>;
  return <ul className="archive-media-feed">{media.map((item) => {
    const video = item.file_type === 'video';
    const label = [item.song?.artist, item.song?.title].filter(Boolean).join(' - ')
      || item.display_name || '공개 미디어';
    const mediaPath = `/archive/media/${item.id}`;
    return <li className="archive-media-card" key={item.id}>
      <div className="archive-media-card-heading">
        <span className="archive-media-kind">{video ? '영상' : '음원'}</span>
        {item.band?.slug && <Link className="bands-return-link archive-band-link" to={`/archive/bands/${encodeURIComponent(item.band.slug)}`}>밴드 페이지로 <span aria-hidden="true">↗</span></Link>}
      </div>
      <Link className="archive-card-preview-link" aria-label={`${label} ${video ? '영상' : '음원'} 미리보기`} to={mediaPath}>
        <MediaPreview item={item} loadPreview={loadPreview} />
      </Link>
      <div className="archive-media-card-details">
        <div><Link className="archive-media-title" to={mediaPath}>{label}</Link>
          {item.rehearsal_title && <p>{item.rehearsal_title}</p>}</div>
        <Link className="primary-btn" to={mediaPath}>{video ? '영상 재생' : '음원 듣기'}</Link>
      </div>
    </li>;
  })}</ul>;
}

export function ArchiveMediaFeed({ loadPage, loadPreview }) {
  const [state, setState] = useState(initialFeed);
  const marker = useRef(null);
  const request = useRef(null);
  const offset = useRef(0);
  const ended = useRef(false);

  const loadNext = useCallback(() => {
    if (request.current || ended.current) return;
    const controller = new AbortController();
    request.current = controller;
    setState((value) => ({ ...value, pending: true, error: false }));
    loadPage(offset.current, PAGE_SIZE, controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      if (!Array.isArray(data?.media)) throw new Error('Invalid archive page');
      offset.current += PAGE_SIZE;
      ended.current = data.pagination?.has_more !== true || data.media.length === 0;
      setState((value) => {
        const byId = new Map(value.media.map((item) => [item.id, item]));
        for (const item of data.media) {
          if (!byId.has(item.id)) byId.set(item.id, item);
        }
        return { media: [...byId.values()], started: true, hasMore: !ended.current, pending: false, error: false };
      });
    }).catch(() => {
      if (!controller.signal.aborted) setState((value) => ({ ...value, pending: false, error: true }));
    }).finally(() => {
      if (request.current === controller) request.current = null;
    });
  }, [loadPage]);

  useEffect(() => {
    let disposed = false;
    queueMicrotask(() => {
      if (!disposed) loadNext();
    });
    return () => {
      disposed = true;
      request.current?.abort();
      request.current = null;
    };
  }, [loadNext]);

  useEffect(() => {
    if (!marker.current || state.pending || state.error || !state.hasMore || typeof IntersectionObserver !== 'function') return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadNext();
    }, { rootMargin: '160px 0px' });
    observer.observe(marker.current);
    return () => observer.disconnect();
  }, [loadNext, state.error, state.hasMore, state.pending, state.started]);

  return <section aria-label="공개 영상·음원">
    {state.started && <ArchiveMediaCards media={state.media} loadPreview={loadPreview} />}
    <div className="archive-feed-status">
      {state.pending && <p role="status">공개 영상·음원을 불러오는 중입니다…</p>}
      {state.error && <p role="alert">공개 영상·음원을 불러오지 못했습니다.</p>}
      {!state.pending && (state.error || (state.started && state.hasMore)) && <div ref={marker}>
        <button type="button" className="primary-btn" onClick={loadNext}>{state.error ? '다시 시도' : '더 보기'}</button>
      </div>}
    </div>
  </section>;
}
