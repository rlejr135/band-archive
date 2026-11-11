import { useContext, useEffect, useState } from 'react';
import { UploadContext } from '../../../context/UploadContext.jsx';
import { isActiveBandMembership } from '../../../services/bandMembership.js';
import { BandUploadInput } from '../BandUploadInput.jsx';

export const RehearsalMediaUpload = ({ band, item, onMediaComplete }) => {
  const upload = useContext(UploadContext);
  const subscribe = upload?.subscribeMediaCompletion;
  const canUpload = Boolean(upload) && isActiveBandMembership(band.membership);
  const [selectedSongId, setSelectedSongId] = useState('');

  useEffect(() => {
    if (!canUpload || !subscribe || !onMediaComplete) return undefined;
    // Uploads keep running if the user changes the selected song.
    const matchesDetail = ({ target, media }) => media?.id && String(target?.bandId) === String(band.id);
    return subscribe({ bandId: band.id, rehearsalId: item.id }, (event) => {
      if (matchesDetail(event) || event.prior?.some(matchesDetail)) onMediaComplete();
    });
  }, [band.id, canUpload, item.id, onMediaComplete, subscribe]);

  if (!canUpload) return null;
  const songs = (item.songs || []).filter((song) => Number.isSafeInteger(song?.id) && song.id > 0);
  const song = songs.length === 1 ? songs[0] : songs.find(({ id }) => String(id) === selectedSongId);
  const rehearsalVisibility = item.effective_visibility || item.visibility;
  const songVisibility = song?.effective_visibility || song?.visibility;
  const parentVisibility = [rehearsalVisibility, songVisibility].includes('MEMBERS') ? 'MEMBERS' : rehearsalVisibility || songVisibility;

  return <section aria-label="합주 미디어 업로드" className="workspace-form">
    {songs.length === 0 && <p className="workspace-muted">미디어를 올리려면 일정에 곡을 연결해 주세요.</p>}
    {songs.length > 1 && <label>업로드할 곡
      <select value={song ? String(song.id) : ''} onChange={(event) => setSelectedSongId(event.target.value)}>
        <option value="">곡 선택…</option>
        {songs.map(({ id, title, artist }) => <option key={id} value={id}>{title}{artist ? ` · ${artist}` : ''}</option>)}
      </select>
    </label>}
    {song && <BandUploadInput band={band} songId={song.id} rehearsalId={item.id} parentVisibility={parentVisibility} />}
  </section>;
};
