// One Capacitor listener and a retained snapshot shared by every upload UI.
const listeners = new Set();
const items = new Map();
let started = false;
let remove = null;

const publish = (item) => { items.set(item.id, item); listeners.forEach((listener) => listener(item)); };

export const hydrateNativeUploadQueue = async (transport) => {
  if (!transport || transport.kind !== 'native') return [];
  if (!started) {
    started = true;
    const pending = await transport.listPending();
    (pending.items || []).forEach(publish);
    remove = await transport.addListener('state', publish);
  }
  return [...items.values()];
};

export const subscribeNativeUploadQueue = (listener) => {
  listeners.add(listener); items.forEach(listener);
  return () => listeners.delete(listener);
};

export const acknowledgeNativeUpload = async (transport, id) => {
  const item = items.get(id);
  if (!item || !['completed', 'failed'].includes(item.state)) return;
  await transport.acknowledge?.({ id }); items.delete(id);
};

export const resetNativeUploadQueueForTest = () => { remove?.(); remove = null; started = false; items.clear(); listeners.clear(); };
