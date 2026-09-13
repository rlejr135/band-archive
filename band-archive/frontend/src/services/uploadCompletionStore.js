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

const completionKey = ({ target, media }) => [
  value(target, 'memberId') || '', value(target, 'songId') || '', value(target, 'rehearsalId') || '', media?.id || '',
].join(':');

// App-lifetime completion store. The retained snapshots let a target that
// mounts after a durable native item was ACKed still refresh its media list.
export const createUploadCompletionStore = () => {
  const events = new Map();
  const subscribers = new Set();
  let version = 0;
  const matchingEvents = (target) => [...events.values()].filter((event) => uploadTargetsMatch(target, event.target));
  return {
    publish: ({ target, media, nativeId = null }) => {
      if (!media?.id) return false;
      const key = completionKey({ target, media });
      if (events.has(key)) return false;
      const event = { target, media, nativeId, version: ++version };
      events.set(key, event);
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
