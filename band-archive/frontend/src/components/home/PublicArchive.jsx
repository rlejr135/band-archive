import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';
import { workspaceMediaFile } from '../../services/playbackMedia.js';
import { safeExternalHttpUrl } from '../../services/safeExternalUrl.js';
import GlobalShell from '../layout/GlobalShell.jsx';
import MediaPlayer from '../common/MediaPlayer.jsx';
import { ArchiveMediaCards, ArchiveMediaFeed } from './ArchiveMediaFeed.jsx';
import './PublicArchive.css';

const api = createAuthenticatedApiClient({ fetchImpl: (...args) => fetch(...args) });
const base = '/api/v2/public/archive';
const archiveBand = (slug) => `/archive/bands/${encodeURIComponent(slug)}`;
const archiveSong = (slug, id) => `${archiveBand(slug)}/songs/${id}`;
const PAGE_SIZE = 20;
const empty = { data: null, pending: true, error: false };

function useArchiveRead(path) {
  const [state, setState] = useState(empty);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api.requestJson(`${base}${path}`, { authenticated: false, signal: controller.signal, cache: 'no-store' }).then(
      (data) => {
        if (!controller.signal.aborted) setState({ data, pending: false, error: false });
      },
      (error) => {
        if (!controller.signal.aborted) setState({ data: null, pending: false, error: error?.status === 404 ? 'missing' : true });
      },
    );
    return () => controller.abort();
  }, [path, attempt]);
  const reload = () => {
    setState(empty);
    setAttempt((value) => value + 1);
  };
  return { ...state, reload };
}

function ArchiveReadState({ read }) {
  if (read.pending) return <p role="status">공개 자료를 불러오는 중입니다…</p>;
  if (read.error === 'missing') return <p role="status">공개된 자료를 찾을 수 없습니다.</p>;
  if (!read.error) return null;
  return <div><p role="alert">공개 자료를 불러오지 못했습니다.</p><button type="button" className="primary-btn" onClick={read.reload}>다시 시도</button></div>;
}

function SongList({ songs, band }) {
  if (!songs?.length) return <p>표시할 공개 곡이 없습니다.</p>;
  return <ul className="public-archive-list">{songs.map((song) => {
    const owner = song.band || band;
    if (!owner?.slug) return null;
    return <li key={song.id}>
      <Link to={archiveSong(owner.slug, song.id)}><strong>{song.title}</strong></Link>
      <span>{[song.artist, owner.name].filter(Boolean).join(' · ')}</span>
    </li>;
  })}</ul>;
}

function PageContent({ path, offset, render, onPrevious, onNext }) {
  const read = useArchiveRead(`${path}?limit=${PAGE_SIZE}&offset=${offset}`);
  return <>
    <ArchiveReadState read={read} />
    {read.data && <>
      {render(read.data)}
      {(offset > 0 || read.data.pagination?.has_more) && <nav className="public-archive-pagination" aria-label="공개 자료 페이지">
        {offset > 0 && <button type="button" className="bands-return-link" onClick={onPrevious}>이전 자료</button>}
        {read.data.pagination?.has_more && <button type="button" className="primary-btn" onClick={onNext}>다음 자료</button>}
      </nav>}
    </>}
  </>;
}

function ArchivePages({ path, render }) {
  const [offset, setOffset] = useState(0);
  return <PageContent key={offset} path={path} offset={offset} render={render}
    onPrevious={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
    onNext={() => setOffset((value) => value + PAGE_SIZE)} />;
}

function BandHeader({ band }) {
  const logo = safeExternalHttpUrl(band.logo_url);
  const image = safeExternalHttpUrl(band.featured_image_url);
  return <header className="public-archive-band">
    <div className="public-archive-heading">
      {logo && <img className="public-archive-logo" src={logo} alt="" />}
      <h1>{band.name}</h1>
    </div>
    {band.description && <p>{band.description}</p>}
    {image && <img className="public-archive-image" src={image} alt={`${band.name} 대표 사진`} />}
  </header>;
}

export function ArchiveBandContent({ band, songs }) {
  return <>
    <BandHeader band={band} />
    <h2>공개 곡</h2><SongList songs={songs} band={band} />
  </>;
}

const requestArchiveMediaPage = (offset, limit, signal) => api.requestJson(
  `${base}/media?limit=${limit}&offset=${offset}`,
  { authenticated: false, signal, cache: 'no-store' },
);

async function requestArchiveVideoPreview(id, signal) {
  const result = await api.requestJson(`${base}/media/${encodeURIComponent(id)}`, {
    authenticated: false, signal, cache: 'no-store',
  });
  if (result?.media?.id !== id || result.media.file_type !== 'video') return null;
  return result.media.playback?.video_url ?? result.media.playback?.url;
}

export function PublicArchivePage() {
  return <GlobalShell><div className="public-archive"><h1>공개 아카이브</h1>
    <ArchiveMediaFeed loadPage={requestArchiveMediaPage} loadPreview={requestArchiveVideoPreview} />
  </div></GlobalShell>;
}

export function PublicArchiveBandPage() {
  const { slug } = useParams();
  return <GlobalShell><div className="public-archive">
    <Link className="bands-return-link" to="/archive"><span aria-hidden="true">←</span> 공개 아카이브</Link>
    <ArchivePages key={slug} path={`/bands/${encodeURIComponent(slug)}`} render={({ band, songs }) => <ArchiveBandContent band={band} songs={songs} />} />
  </div></GlobalShell>;
}

export function PublicArchiveSongPage() {
  const { slug, id } = useParams();
  return <GlobalShell><div className="public-archive">
    <Link className="bands-return-link" to={archiveBand(slug)}><span aria-hidden="true">←</span> 밴드 공개 페이지</Link>
    <ArchivePages key={`${slug}:${id}`} path={`/bands/${encodeURIComponent(slug)}/songs/${encodeURIComponent(id)}`} render={({ band, song, media }) => <>
      <header><p>{band.name}</p><h1>{song.title}</h1>{song.artist && <p>{song.artist}</p>}</header>
      <h2>공개 영상·음원</h2><ArchiveMediaCards media={media} loadPreview={requestArchiveVideoPreview} />
    </>} />
  </div></GlobalShell>;
}

function ArchivePlayback({ id }) {
  const read = useArchiveRead(`/media/${encodeURIComponent(id)}`);
  const triedRefresh = useRef(false);
  if (!read.data) return <ArchiveReadState read={read} />;
  const { band, song, media } = read.data;
  const refreshOnce = () => {
    if (triedRefresh.current) return;
    triedRefresh.current = true;
    read.reload();
  };
  return <>
    <Link className="bands-return-link" to={archiveSong(band.slug, song.id)}><span aria-hidden="true">←</span> {song.title}</Link>
    <header><p>{band.name}</p><h1>{media.display_name || song.title}</h1>{song.artist && <p>{song.artist}</p>}</header>
    <MediaPlayer file={workspaceMediaFile(media)} playbackIdentity={`public-archive:${id}`} preload="none" sharedModeButtons onPlaybackError={refreshOnce} onRefresh={read.reload} />
    <button type="button" className="primary-btn" onClick={read.reload}>재생 주소 새로고침</button>
  </>;
}

export function PublicArchiveMediaPage() {
  const { id } = useParams();
  return <GlobalShell><div className="public-archive"><ArchivePlayback key={id} id={id} /></div></GlobalShell>;
}
