import { PublicArchiveLinks } from '../media/PublicArchiveLinks.jsx';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';
import { useVisibilityChange } from '../media/useVisibilityChange.js';

export function SongPublicOverride({ song, band, api, onSaved }) {
  const { change, busy, error } = useVisibilityChange(
    api, bandWorkspacePaths.songPublicOverride(band.id, song.id), 'song', song.id,
  );
  if (!song.can_change_public_override) return null;
  const enabled = song.public_override === true;
  const toggle = () => {
    const next = !enabled;
    if (!next && !window.confirm('이 곡의 개별 공개 설정을 해제할까요? 영상·음원의 공개 설정은 보존됩니다.')) return;
    const confirmation = next
      ? `“${song.title}”${song.artist ? ` · ${song.artist}` : ''} 곡 정보와 공개 가능한 영상·음원을 로그인 없이 볼 수 있게 합니다. 게시·공유 권한과 필요한 출연자·권리자의 허락을 확인하셨나요?`
      : undefined;
    change({ public_override: next }, onSaved, confirmation);
  };
  return <div className="workspace-media-visibility">
    <div className="detail-heading">
      <button type="button" className="secondary-btn" disabled={busy} onClick={toggle}>{busy ? '저장 중…' : enabled ? '이 곡의 개별 공개 해제' : '이 곡만 공개'}</button>
      {song.archive_visibility === 'PUBLIC' && <PublicArchiveLinks path={`/archive/bands/${encodeURIComponent(band.slug)}/songs/${song.id}`} label="공개 곡 페이지" />}
    </div>
    <p className="workspace-muted">밴드가 멤버 전용이어도 이 곡을 공개할 수 있습니다. 재생은 공개 범위와 합주 제한을 충족하는 영상·음원에 한해 제공됩니다.</p>
    {error && <p role="alert">{error}</p>}
  </div>;
}
