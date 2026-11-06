import { useEffect, useRef, useState } from 'react';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { requestOptions } from '../workspaceReadSupport.js';

export const RehearsalSongPicker = (props) => <RehearsalSongPickerContent key={props.bandId} {...props} />;

const RehearsalSongPickerContent = ({ bandId, api, selectedIds, initialSongs = [], onChange }) => {
  const [songs, setSongs] = useState(initialSongs);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const originalSongs = useRef(initialSongs);
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      const all = new Map(originalSongs.current.map((song) => [String(song.id), song]));
      let offset = 0;
      do {
        const response = await api.requestJson(bandWorkspacePaths.songs(bandId, { limit: 100, offset }), requestOptions(controller.signal));
        if (controller.signal.aborted) return;
        for (const song of response?.songs || []) all.set(String(song.id), song);
        const page = response?.pagination;
        if (!page?.has_more) break;
        if (!Number.isInteger(page.next_offset) || page.next_offset <= offset) throw new Error('Invalid pagination');
        offset = page.next_offset;
      } while (!controller.signal.aborted);
      if (!controller.signal.aborted) setSongs([...all.values()]);
    };
    load().catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, bandId, attempt]);
  const search = query.trim().toLocaleLowerCase();
  const filtered = songs.filter((song) => `${song.title} ${song.artist || ''}`.toLocaleLowerCase().includes(search));
  return <fieldset className="rehearsal-song-picker"><legend>연결할 곡</legend>
    <div className="rehearsal-song-picker-heading"><label>곡 검색<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="곡명 또는 아티스트" /></label><span aria-live="polite">{selectedIds.length}곡 선택</span></div>
    {loading && <p role="status">곡 목록을 불러오는 중입니다…</p>}
    {error && <p role="alert">곡 목록을 불러오지 못했습니다. <button type="button" className="secondary-btn" onClick={() => { setLoading(true); setError(false); setAttempt((value) => value + 1); }}>다시 시도</button></p>}
    <div className="rehearsal-song-options" role="group" aria-label="연결할 곡 선택">
      {filtered.map((song) => { const id = String(song.id); const selected = selectedIds.includes(id); return <label className={`rehearsal-song-option${selected ? ' is-selected' : ''}`} key={id}><input type="checkbox" checked={selected} onChange={(event) => onChange(event.target.checked ? [...selectedIds, id] : selectedIds.filter((value) => value !== id))} /><span><strong>{song.title}</strong>{song.artist && <small>{song.artist}</small>}</span>{selected && <small className="rehearsal-song-selected">선택됨</small>}</label>; })}
      {!loading && !error && filtered.length === 0 && <p className="workspace-muted">{search ? '검색 결과가 없습니다.' : '연결할 곡이 없습니다.'}</p>}
    </div>
  </fieldset>;
};
