// The v2 comments endpoint deliberately returns a flat page.  Keep the
// reconstruction client-side, deterministic, and defensive against historic
// malformed parent links (including cycles and parents outside this page).
const commentOrder = (left, right) => {
  const leftTime = left.created_at || '';
  const rightTime = right.created_at || '';
  return leftTime.localeCompare(rightTime) || Number(left.id) - Number(right.id);
};

export const mergeCommentPage = (comments, page) => {
  const known = new Set();
  return [...comments, ...(Array.isArray(page) ? page : [])]
    .filter((comment) => comment?.id != null && !known.has(comment.id) && (known.add(comment.id), true))
    .sort(commentOrder);
};

export const buildCommentThreads = (comments, maxDepth = 8) => {
  const items = mergeCommentPage([], comments);
  const byId = new Map(items.map((item) => [item.id, { ...item, replies: [] }]));
  const roots = [];

  // A parent is usable only if following its chain cannot return to this
  // comment. Missing parents are deliberately rendered as roots.
  const safeParent = (item) => {
    if (item.parent_id == null || !byId.has(item.parent_id)) return null;
    const seen = new Set([item.id]);
    let cursor = byId.get(item.parent_id);
    while (cursor) {
      if (seen.has(cursor.id)) return null;
      seen.add(cursor.id);
      cursor = cursor.parent_id == null ? null : byId.get(cursor.parent_id);
    }
    return byId.get(item.parent_id);
  };
  const depthFor = (item, seen = new Set()) => {
    if (seen.has(item.id)) return maxDepth;
    const parent = safeParent(item);
    return parent ? 1 + depthFor(parent, new Set([...seen, item.id])) : 0;
  };

  items.forEach((item) => {
    const node = byId.get(item.id);
    const parent = safeParent(item);
    if (!parent || depthFor(item) >= maxDepth) roots.push(node);
    else parent.replies.push(node);
  });
  const sortTree = (nodes) => nodes.sort(commentOrder).forEach((node) => sortTree(node.replies));
  sortTree(roots);
  return roots;
};

export const voteTarget = (currentVote, clickedValue) => currentVote === clickedValue ? 0 : clickedValue;
