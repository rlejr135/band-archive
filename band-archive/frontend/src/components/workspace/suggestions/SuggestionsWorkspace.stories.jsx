import { useMemo, useRef } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { AuthContext } from '../../../context/AuthContext.jsx';
import { WorkspaceContext } from '../WorkspaceContext.jsx';
import { SuggestionsWorkspace } from './SuggestionsWorkspace.jsx';

const defaults = {
  id: 1,
  title: 'Big Buck Bunny',
  artist: 'Blender Foundation',
  memo: '카드에서 바로 재생하는 예시입니다.',
  link: 'https://www.youtube.com/watch?v=YE7VzlLtp-4',
  visibility: 'MEMBERS',
  status: 'pending',
  can_edit: true,
  can_delete: true,
  can_proceed: true,
  vote_score: 2,
};

const previewAuth = { user: { id: 'storybook' }, getAccessToken: async () => null };
const band = { id: 1, name: '예시 밴드', default_visibility: 'MEMBERS', membership: { id: 1, status: 'active' } };

const SuggestionsPreview = ({ width = 780, showExternalLink = false }) => {
  const entries = useRef(null);
  entries.current ??= [
    defaults,
    ...(showExternalLink ? [{ ...defaults, id: 2, title: '참고 링크 예시', link: 'https://example.com/', can_edit: false, can_delete: false, can_proceed: false }] : []),
  ];
  const workspace = useMemo(() => ({
    band,
    workspaceKey: 'suggestions-story',
    api: {
      requestJson: async (path, { method = 'GET', body } = {}) => {
        if (method === 'GET') return { suggestions: entries.current };
        const id = Number(path.match(/\/suggestions\/(\d+)/)?.[1]);
        if (method === 'PATCH') {
          const changes = JSON.parse(body);
          entries.current = entries.current.map((entry) => entry.id === id ? { ...entry, ...changes } : entry);
        } else if (method === 'PUT') {
          const { value: vote } = JSON.parse(body);
          entries.current = entries.current.map((entry) => entry.id === id ? {
            ...entry, viewer_vote: vote, vote_score: entry.vote_score + vote - (entry.viewer_vote || 0),
          } : entry);
        } else if (method === 'DELETE' || (method === 'POST' && id)) {
          entries.current = entries.current.filter((entry) => entry.id !== id);
        } else if (method === 'POST') {
          entries.current = [...entries.current, { ...defaults, ...JSON.parse(body), id: Date.now(), vote_score: 0 }];
        }
        return {};
      },
    },
  }), []);
  return <MemoryRouter><AuthContext.Provider value={previewAuth}><WorkspaceContext.Provider value={workspace}>
    <div className="band-workspace" style={{ maxWidth: width, minHeight: 0 }}>
      <main className="workspace-main"><SuggestionsWorkspace /></main>
    </div>
  </WorkspaceContext.Provider></AuthContext.Provider></MemoryRouter>;
};

export default { title: 'Band Workspace/Song Suggestions', component: SuggestionsPreview };

export const YoutubePlayback = {};
export const MobileAndExternalLink = { args: { width: 393, showExternalLink: true } };
