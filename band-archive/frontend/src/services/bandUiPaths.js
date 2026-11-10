const bandBase = (slug) => typeof slug === 'string' && slug.trim()
  ? `/bands/${encodeURIComponent(slug)}` : null;
const positiveId = (value) => {
  if ((typeof value !== 'string' && typeof value !== 'number') || !/^[1-9]\d*$/.test(String(value))) return null;
  return Number.isSafeInteger(Number(value)) ? Number(value) : null;
};
const detailPath = (slug, id, suffix) => {
  const base = bandBase(slug);
  const targetId = positiveId(id);
  return base && targetId ? `${base}/${suffix(targetId)}` : null;
};

// Browser routes deliberately differ from the band-scoped API paths.
export const bandUiPaths = {
  song: (slug, id) => detailPath(slug, id, (targetId) => `songs?song=${targetId}`),
  rehearsal: (slug, id) => detailPath(slug, id, (targetId) => `rehearsals?rehearsal=${targetId}`),
  member: (slug, id) => detailPath(slug, id, (targetId) => `members/${targetId}`),
};

export const uploadTargetPath = (task) => {
  if (task?.target?.memberId != null) return bandUiPaths.member(task.bandSlug, task.target.memberId);
  if (task?.target?.songId != null) return bandUiPaths.song(task.bandSlug, task.target.songId);
  if (task?.target?.rehearsalId != null) return bandUiPaths.rehearsal(task.bandSlug, task.target.rehearsalId);
  return null;
};
