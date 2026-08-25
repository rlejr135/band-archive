import { isSongVoteSnapshot } from './songVoting.js';

export const SONG_VOTE_CHANNEL_NAME = 'anything-song-votes-v1';
const SONG_UPDATED = 'song-updated';

export const isSongVoteChannelMessage = (message) => (
  message
  && message.type === SONG_UPDATED
  && isSongVoteSnapshot(message.song)
);

/** A best-effort, same-origin tab sync channel. It contains no voter identity. */
export const createSongVoteChannel = ({ BroadcastChannelImpl = globalThis.BroadcastChannel } = {}) => {
  if (typeof BroadcastChannelImpl !== 'function') return null;
  let channel;
  try {
    channel = new BroadcastChannelImpl(SONG_VOTE_CHANNEL_NAME);
  } catch {
    return null;
  }

  return {
    publish: (song) => {
      if (isSongVoteSnapshot(song)) channel.postMessage({ type: SONG_UPDATED, song });
    },
    subscribe: (listener) => {
      const handleMessage = (event) => {
        if (isSongVoteChannelMessage(event?.data)) listener(event.data.song);
      };
      channel.addEventListener('message', handleMessage);
      return () => channel.removeEventListener('message', handleMessage);
    },
    close: () => channel.close(),
  };
};
