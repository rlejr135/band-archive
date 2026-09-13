import React, { useCallback, useRef } from 'react';
import { getMediaType } from '../../services/mediaPresentation';
import CommentSection from './CommentSection';
import MediaPlayer from './MediaPlayer';

// Keeps one controller ref beside one comment target, avoiding cross-card
// seeking when a screen renders several media players at once.
const MediaDiscussion = ({ file, onMediaUpdate, mediaKind = 'media', targetType, targetId }) => {
  const playbackControllerRef = useRef(null);
  const setPlaybackController = useCallback((controller) => { playbackControllerRef.current = controller; }, []);
  const type = getMediaType(file);
  const timestampEnabled = type === 'audio' || type === 'video';

  return <>
    <MediaPlayer file={file} onMediaUpdate={onMediaUpdate} mediaKind={mediaKind} onPlaybackController={setPlaybackController} />
    <CommentSection targetType={targetType} targetId={targetId} timestampEnabled={timestampEnabled} playbackControllerRef={playbackControllerRef} />
  </>;
};

export default MediaDiscussion;
