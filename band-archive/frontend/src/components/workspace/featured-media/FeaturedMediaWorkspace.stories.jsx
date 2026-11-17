import { useMemo } from 'react';
import { MemoryRouter } from 'react-router-dom';
import poster from '../../home/archive-story-stage.png';
import { WorkspaceContext } from '../WorkspaceContext.jsx';
import '../BandWorkspace.css';
import { RepresentativeMediaWorkspace } from './FeaturedMediaWorkspace.jsx';
import videoFixture from './storybook-fixtures/sample.webm?no-inline';
import audioFixture from './storybook-fixtures/sample.wav?no-inline';

const band = { id: 1, slug: 'night-wave', name: '나이트 웨이브' };
const absolute = (asset) => new URL(asset, window.location.href).href;

const createWorkspace = (canFeature) => {
  const candidates = [
    {
      id: 11,
      song_summary: { id: 21, title: '새벽의 합주', artist: '나이트 웨이브' },
      display_name: '11월 합주 영상',
      created_at: '2026-11-13T12:00:00Z',
      file_type: 'video',
      poster_url: absolute(poster),
      playback: { video_url: absolute(videoFixture), audio_url: absolute(audioFixture) },
      can_feature: canFeature,
    },
    {
      id: 12,
      song_summary: { id: 22, title: '밤에 듣는 베이스', artist: '나이트 웨이브' },
      display_name: '어쿠스틱 연습 녹음',
      created_at: '2026-11-08T12:00:00Z',
      file_type: 'audio',
      playback: { audio_url: absolute(audioFixture) },
      can_feature: canFeature,
    },
  ];
  let media = candidates;
  return {
    band,
    workspaceKey: 'featured-story',
    api: {
      requestJson: async (path, { method = 'GET' } = {}) => {
        const songId = Number(path.match(/\/songs\/(\d+)/)?.[1]);
        if (method === 'DELETE') {
          media = media.filter((item) => item.song_summary.id !== songId);
          return {};
        }
        if (method === 'PUT') {
          media = [...media.filter((item) => item.song_summary.id !== songId), ...candidates.filter((item) => item.song_summary.id === songId)];
          return {};
        }
        if (path.includes('/songs?')) return { songs: candidates.map((item) => ({ ...item.song_summary, can_feature: canFeature })) };
        if (songId) {
          return { media: candidates.filter((item) => item.song_summary.id === songId).map((item) => ({
            ...item, song_id: songId, is_featured: media.includes(item),
          })) };
        }
        return { media };
      },
    },
  };
};

const FeaturedPreview = ({ width = 1000, canFeature = true }) => {
  const workspace = useMemo(() => createWorkspace(canFeature), [canFeature]);
  return <MemoryRouter>
    <WorkspaceContext.Provider value={workspace}>
      <div className="band-workspace" style={{ maxWidth: width, minHeight: 0 }}>
        <main className="workspace-main"><RepresentativeMediaWorkspace /></main>
      </div>
    </WorkspaceContext.Provider>
  </MemoryRouter>;
};

export default { title: 'Band Workspace/Featured Media', component: FeaturedPreview };
export const DesktopCards = {};
export const MobileCards = { args: { width: 393 } };
export const MemberCards = { args: { canFeature: false } };
