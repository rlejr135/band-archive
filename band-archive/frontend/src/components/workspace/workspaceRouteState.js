import { createElement } from 'react';

export const workspaceIdentityKey = (slug, userId) => `${slug || ''}:${userId || 'visitor'}`;

// A small request generation guard keeps late responses from an old URL or
// account from committing after the route has moved on.
export const createWorkspaceRequestGate = () => {
  let generation = 0;
  let activeKey = '';
  return {
    begin(key) { generation += 1; activeKey = key; return generation; },
    accepts(key, token) { return activeKey === key && generation === token; },
  };
};

// Used by the route shell as an additional render-time fence. A resolved A
// band can never paint while the URL has already requested B, even before a
// new fetch effect begins.
export const WorkspacePaintGate = ({ requestedSlug, resolvedSlug, children }) => (
  requestedSlug && requestedSlug === resolvedSlug
    ? children
    : createElement('main', { className: 'band-workspace-loading', 'data-workspace-state': 'loading', 'aria-live': 'polite' }, '밴드 공간을 여는 중입니다…')
);

export const mergeMediaPage = (pages, page) => [...pages.filter((value) => value.offset !== page.offset), page]
  .sort((left, right) => left.offset - right.offset);

export const takeOneStaleRefresh = (attempts, pageOffset, mediaId) => {
  const key = `${pageOffset}:${mediaId}`;
  if (attempts.has(key)) return false;
  attempts.add(key);
  return true;
};
