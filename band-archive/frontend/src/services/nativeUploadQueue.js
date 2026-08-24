// One native listener and one retained snapshot shared by every upload surface.
const listeners = new Set();
const items = new Map();
const acknowledging = new Set();
const recovered = new Set();
let started = false;
let remove = null;
let hydration = null;
const terminalStates = new Set(['completed', 'failed', 'cancelled']);

const snapshot = () => [...items.values()];
const notify = () => { const value = snapshot(); listeners.forEach((listener) => listener(value)); };
const sameValue = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const publish = (item) => { if (!item?.id || sameValue(items.get(item.id), item)) return; items.set(item.id, item); notify(); };
const numeric = (value) => value === undefined || value === null || value === '' ? null : Number(value);

export const nativeTargetMatches = (item, target = {}) => {
  const value = item?.target || {};
  const songId = numeric(target.songId ?? target.song_id);
  const rehearsalId = numeric(target.rehearsalId ?? target.rehearsal_id);
  const memberId = numeric(target.memberId ?? target.member_id);
  const expectsRehearsal = Object.hasOwn(target,'rehearsalId') || Object.hasOwn(target,'rehearsal_id');
  if (memberId !== null) return numeric(value.member_id) === memberId && numeric(value.song_id) === null && numeric(value.rehearsal_id) === null;
  if (numeric(value.member_id) !== null) return false;
  if (songId !== null && numeric(value.song_id) !== songId) return false;
  if (expectsRehearsal && numeric(value.rehearsal_id) !== rehearsalId) return false;
  if (rehearsalId !== null && numeric(value.rehearsal_id) !== rehearsalId) return false;
  return songId !== null || rehearsalId !== null;
};
export const nativeUploadResult = (item) => { try { const value = typeof item?.result === 'string' ? JSON.parse(item.result) : item?.result; return value?.media || value?.personal_log || value || null; } catch { return null; } };
export const nativeUploadKind = (item) => item?.target?.member_id ? 'personal_log' : 'media';
export const filterNativeUploads = (target) => snapshot().filter((item) => nativeTargetMatches(item, target));
export const mergeNativeUploadState = (previous, matching, project = (item) => item) => {
  let next = previous;
  matching.forEach((item) => {
    const value = project(item);
    if (sameValue(previous[item.id], value)) return;
    if (next === previous) next = { ...previous };
    next[item.id] = value;
  });
  return next;
};
export const updateNativeUpload = (item) => publish({ ...items.get(item.id), ...item });
export const claimNativeProcessingRecovery = (id) => { if (recovered.has(id)) return false; recovered.add(id); return true; };
export const releaseNativeProcessingRecovery = (id) => recovered.delete(id);

export const hydrateNativeUploadQueue = async (transport, { refresh = false } = {}) => {
  if (!transport || transport.kind !== 'native') return [];
  if (hydration) await hydration;
  if (!started || refresh) {
    hydration = (async () => {
      const pending = await transport.listPending();
      (pending.items || []).forEach(publish);
      if (!started) { started = true; remove = await transport.addListener('state', publish); }
    })();
    try { await hydration; } finally { hydration = null; }
  }
  return snapshot();
};

export const subscribeNativeUploadQueue = (listener) => { listeners.add(listener); listener(snapshot()); return () => listeners.delete(listener); };

export const acknowledgeNativeUpload = async (transport, id, target) => {
  const item = items.get(id);
  if (!item || !terminalStates.has(item.state) || (target && !nativeTargetMatches(item, target)) || acknowledging.has(id)) return false;
  acknowledging.add(id);
  try { await transport.acknowledge?.({ id }); items.delete(id); notify(); return true; } finally { acknowledging.delete(id); }
};

export const consumeNativeUpload = async (transport, id, target, consume) => {
  const item = items.get(id);
  if (!item || !terminalStates.has(item.state) || !nativeTargetMatches(item, target) || acknowledging.has(id)) return false;
  acknowledging.add(id);
  try { await consume?.(item); await transport.acknowledge?.({ id }); items.delete(id); notify(); return true; } finally { acknowledging.delete(id); }
};

export const deleteNativeUpload = async (transport, id, target) => {
  const item = items.get(id);
  if (!item || !terminalStates.has(item.state) || !nativeTargetMatches(item, target) || acknowledging.has(id)) return false;
  acknowledging.add(id);
  try { await (transport.delete || transport.acknowledge)?.({ id }); items.delete(id); notify(); return true; } finally { acknowledging.delete(id); }
};

export const syncNativeProcessingStatus = async (transport, id, state, media) => {
  const item = items.get(id); if (!item || !['completed', 'failed'].includes(state)) return null;
  const result = media ? JSON.stringify(media) : item.result;
  const updated = { ...item, state, result, error: state === 'failed' ? (media?.processing_error || media?.error || item.error) : item.error };
  publish(updated); await transport.syncStatus?.({ id, state, result, error: updated.error }); return updated;
};

export const resetNativeUploadQueueForTest = () => { remove?.(); remove = null; started = false; hydration = null; items.clear(); listeners.clear(); acknowledging.clear(); recovered.clear(); };
