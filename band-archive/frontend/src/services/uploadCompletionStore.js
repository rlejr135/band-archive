const value = (item, name) => item?.[name] === undefined || item?.[name] === null || item?.[name] === '' ? null : String(item[name]);

export const uploadTargetsMatch = (subscriber = {}, event = {}) => {
  const subscriberMember = value(subscriber, 'memberId');
  const eventMember = value(event, 'memberId');
  if (subscriberMember || eventMember) return subscriberMember !== null && subscriberMember === eventMember;
  const subscriberSong = value(subscriber, 'songId');
  const eventSong = value(event, 'songId');
  if (subscriberSong !== null && subscriberSong !== eventSong) return false;
  const subscriberRehearsal = value(subscriber, 'rehearsalId');
  const eventRehearsal = value(event, 'rehearsalId');
  if (subscriberRehearsal !== null && subscriberRehearsal !== eventRehearsal) return false;
  return subscriberSong !== null || subscriberRehearsal !== null;
};

const completionKey = ({ target }) => [
  value(target, 'memberId') || '', value(target, 'songId') || '', value(target, 'rehearsalId') || '',
].join(':');

const MAX_COMPLETION_TARGETS = 100;

// App-lifetime completion store. The retained snapshots let a target that
// mounts after a durable native item was ACKed still refresh its media list.
export const createUploadCompletionStore = () => {
  // One current snapshot per target, capped globally. Older events cannot help
  // a late route after a newer target completion has already made it dirty.
  const events = new Map();
  const subscribers = new Set();
  let version = 0;
  const matchingEvents = (target) => [...events.values()].filter((event) => uploadTargetsMatch(target, event.target));
  return {
    publish: ({ target, media, nativeId = null }) => {
      if (!media?.id) return false;
      const key = completionKey({ target });
      if (events.get(key)?.media?.id === media.id) return false;
      const event = { target, media, nativeId, version: ++version };
      events.delete(key);
      events.set(key, event);
      while (events.size > MAX_COMPLETION_TARGETS) events.delete(events.keys().next().value);
      subscribers.forEach((subscriber) => {
        if (uploadTargetsMatch(subscriber.target, target)) subscriber.listener(event);
      });
      return true;
    },
    subscribe: (target, listener) => {
      const subscriber = { target, listener };
      subscribers.add(subscriber);
      const prior = matchingEvents(target);
      if (prior.length) listener({ target, media: null, prior, version });
      return () => subscribers.delete(subscriber);
    },
    versionFor: (target) => matchingEvents(target).reduce((latest, event) => Math.max(latest, event.version), 0),
  };
};
