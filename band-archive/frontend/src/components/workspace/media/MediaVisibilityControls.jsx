import { useVisibilityChange } from './useVisibilityChange.js';
import { PublicArchiveLinks } from './PublicArchiveLinks.jsx';
import { bandWorkspacePaths } from '../../../services/bandWorkspaceApi.js';

const reasons = {
  band: '밴드가 멤버 전용입니다.',
  song: '곡이 멤버 전용입니다.',
  rehearsal: '연결된 합주가 멤버 전용입니다.',
  unavailable: '공개 재생에 필요한 파일이 없습니다.',
};

export function MediaVisibilityControls({ media, band, api, onSaved }) {
  const { change, busy, error } = useVisibilityChange(
    api, bandWorkspacePaths.mediaVisibility(band.id, media.id), 'media', media.id,
  );
  if (!media.can_change_visibility) return null;
  const setVisibility = (visibility) => {
    if (busy || visibility === media.visibility) return;
    const confirmation = visibility === 'PUBLIC'
      ? '공개 가능한 영상·음원은 로그인 없이 볼 수 있습니다. 기존 공개 조건을 충족하면 댓글도 공개될 수 있습니다. 게시·공유 권한과 필요한 출연자·권리자의 허락을 확인하셨나요?'
      : undefined;
    change({ visibility }, onSaved, confirmation);
  };

  return <div className="workspace-media-visibility">
    <div className="detail-heading" role="group" aria-label="영상·음원 공개 범위" aria-busy={busy}>
      <button type="button" className="secondary-btn" aria-pressed={media.visibility === 'MEMBERS'} disabled={busy} onClick={() => setVisibility('MEMBERS')}>멤버 전용</button>
      <button type="button" className="secondary-btn" aria-pressed={media.visibility === 'PUBLIC'} disabled={busy} onClick={() => setVisibility('PUBLIC')}>공개</button>
      {media.archive_visibility === 'PUBLIC' && <PublicArchiveLinks path={`/archive/media/${media.id}`} label="공개 재생 페이지" />}
    </div>
    {media.visibility === 'PUBLIC' && media.archive_visibility === 'MEMBERS' && <p className="workspace-muted">
      공개로 설정됨 · {reasons[media.public_block_reason] ?? '상위 항목의 공개 범위로 제한됩니다.'} 외부에서는 재생할 수 없습니다.
    </p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
