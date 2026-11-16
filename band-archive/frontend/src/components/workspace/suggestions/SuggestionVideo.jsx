import { useState } from 'react';

export const SuggestionVideo = ({ videoId, title }) => {
  const [playing, setPlaying] = useState(false);
  const [thumbnailFailed, setThumbnailFailed] = useState(false);

  return <div className="suggestion-video">
    <button
      type="button"
      className={playing ? 'secondary-btn' : 'secondary-btn suggestion-video-preview'}
      aria-expanded={playing}
      onClick={() => setPlaying((current) => !current)}
    >
      {!playing && !thumbnailFailed && <img
        className="suggestion-video-thumbnail"
        src={`https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`}
        alt=""
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setThumbnailFailed(true)}
      />}
      <span className={playing ? undefined : 'suggestion-video-play-label'}>
        {!playing && <span aria-hidden="true">▶</span>}
        {playing ? '플레이어 닫기' : '카드에서 YouTube 재생'}
      </span>
    </button>
    {playing && <iframe
      className="suggestion-video-player"
      title={`${title || '추천곡'} YouTube 동영상`}
      src={`https://www.youtube-nocookie.com/embed/${videoId}?autoplay=1&playsinline=1`}
      referrerPolicy="strict-origin-when-cross-origin"
      allow="autoplay; encrypted-media; picture-in-picture"
      allowFullScreen
    />}
  </div>;
};
