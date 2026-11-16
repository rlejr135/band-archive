import { useState } from 'react';

export const SuggestionVideo = ({ videoId, title }) => {
  const [playing, setPlaying] = useState(false);

  return <div className="suggestion-video">
    <button
      type="button"
      className="secondary-btn"
      aria-expanded={playing}
      onClick={() => setPlaying((current) => !current)}
    >
      {playing ? '플레이어 닫기' : '카드에서 YouTube 재생'}
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
