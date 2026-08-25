import { isMediaVoteSnapshot } from './mediaVoting.js';

export const MEDIA_VOTE_CHANNEL_NAME = 'anything-media-votes-v1';
const MEDIA_UPDATED = 'media-updated';

export const isMediaVoteChannelMessage = (message) => (
  message
  && message.type === MEDIA_UPDATED
  && isMediaVoteSnapshot(message.media)
);

/** Same-origin tab sync only. Voter UUIDs are never sent through this channel. */
export const createMediaVoteChannel = ({ BroadcastChannelImpl = globalThis.BroadcastChannel } = {}) => {
  if (typeof BroadcastChannelImpl !== 'function') return null;
  let channel;
  try {
    channel = new BroadcastChannelImpl(MEDIA_VOTE_CHANNEL_NAME);
  } catch {
    return null;
  }

  return {
    publish: (media) => {
      if (isMediaVoteSnapshot(media)) channel.postMessage({ type: MEDIA_UPDATED, media });
    },
    subscribe: (listener) => {
      const handleMessage = (event) => {
        if (isMediaVoteChannelMessage(event?.data)) listener(event.data.media);
      };
      channel.addEventListener('message', handleMessage);
      return () => channel.removeEventListener('message', handleMessage);
    },
    close: () => channel.close(),
  };
};
