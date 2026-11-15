import { MemoryRouter } from 'react-router-dom';
import MediaPlayer from '../common/MediaPlayer.jsx';
import { ArchiveMediaFeed } from './ArchiveMediaFeed.jsx';
import poster from './archive-story-stage.png';
import '../bands/BandsPage.css';
import './PublicArchive.css';

const band = { slug: 'night-wave', name: '나이트 웨이브' };
const clips = [
  ['새벽의 합주', 'video'],
  ['밤에 듣는 베이스', 'audio'],
  ['금요일 라이브', 'video'],
  ['어쿠스틱 데모', 'audio'],
  ['앵콜 리허설', 'video'],
  ['첫 번째 연습 테이크', 'audio'],
  ['작은 무대', 'video'],
  ['마지막 세션', 'audio'],
].map(([title, file_type], index) => ({
  id: index + 1,
  file_type,
  band,
  song: { id: index + 1, title, artist: band.name },
  poster_url: file_type === 'video' ? new URL(poster, window.location.origin).href : undefined,
}));

const loadPage = async (offset, limit) => ({
  media: clips.slice(offset, offset + limit),
  pagination: { has_more: offset + limit < clips.length },
});

const ArchiveFeedStory = () => <MemoryRouter initialEntries={['/archive']}>
  <main className="public-archive">
    <h1>공개 아카이브</h1>
    <ArchiveMediaFeed loadPage={loadPage} />
  </main>
</MemoryRouter>;

export default {
  title: 'Public Archive/Media Feed',
  component: ArchiveFeedStory,
};

export const ScrollFeed = {};


// Embedded synthetic media keeps this story independent of public or private files.
const playbackDemo = {
  type: 'video',
  name: '새벽의 합주',
  url: "data:video/webm;base64,GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZ0LGFUmpZqoq17GDD0JARImIQI9AAAAAAABNgIlTdG9yeWJvb2tXQYlTdG9yeWJvb2sWVK5ro66h14EBc8WBAYOBASPjg4QdzWUAhoVWX1ZQOOCGsIFguoEqH0O2dUJp54EAo0EigQAAgBAKAJ0BKmAAKgA+2V6lTKglo6IwGOxxABsJY2vbVoqlYJp1oQFg+qj20k5mbGvfZNk9dBf7fC0KlStkYROFm3WOj7Gkt81+QS82dnytuQQLaG93GmnAx9SAAP7s2fhdafzM/JGbJUSdCPA+svYuCrlizt1WpYGkvODXaxOluF37eBDX8O9lE7eai9+7X5a5Wi2PKjTy4pBnKpQ4SAr6NyCqMyeAOsLamqgUyKMOQsBCHmvUHqupMd47SsHaYierrqo/neDp3UAYjQnDaiG+NA7EjoRm/1TjQbwl/C3t8y60xbaDlshRnF4Mvtx5B36agINi5MwhfiusDLSbkH/VDQdr79Hpe2Pu0HjDXlxWATtHcFDySO7X/3hvoAHOAACjQT6BAfSAMAwAnQEqYAAqAD7RVKJMqCSjIjVYDVEAGgljALvwE+6KMIiq7IkGb+E5sh3G0l/z88c6dGuCtbLCf0S0TuNoAThDYt9NCqxZh4Hdw+cNj4Us+CoOQRSZdqYxEzaNGsBvpd0o2zxxBljoAAD+6dbpse5SiF2wX4SYK6T/nSjJ/EpCvRuq4KswZTTrcj5xU25skDnBzy9Wsn76EggOc0IJkUMrRSYjgM9VroyBsyVNH2zn/Kt9aTCmpQ6OEwVK+YyH+86LNjdRXXQjkAFc1G1aBNe44/YM804sZeiXuuD28ljFP0XVThZ7ShMmi4mG5XS218nEDL986NGfJDvyhc6nm9LMetaPLqJcMbFgpxas8uU63q0wHvvjAm42MB178Imx9Ipfk1kmPIeHTU55iyVLVTZLl5lOoBvgAAA=",
  audio_url: "data:audio/wav;base64,UklGRgwEAABXQVZFZm10IBAAAAABAAEAoA8AAKAPAAABAAgAZGF0YegDAACAgICAgICAgICAf39/f35+f39/gICBgYGBgYCAf35+fX19fn5/gIGCgoOCgoGAf359fHx8fH1/gIKDhISEg4KBf318e3p7e31+gIKDhYWFhIOBf317enl5ent9gIKEhoaHhoSCgH17eXh4eXp9f4KFhoiIh4WDgH16eHd2d3l8f4KFh4mJiIeEgX16d3Z1dnh6foGFiIqKioiFgn56d3V0dXZ5fYGFiIuMi4mGg356d3Rzc3V4fICFiIuNjIuIhH96dnNycnR3e3+EiYyOjoyJhYB7dnNxcXJ1eX+EiIyOj46LhoF8d3NwcHF0eH2DiIyPkI+MiIJ8d3Nwb29yd3yCiI2QkZCNiYN9d3Nvbm5xdXuBh4yQkpGPioV+eHNvbW1wdHqAh4yQkpKQjIaAeXNvbGxucnh/hoyQk5ORjYeBenRvbGttcXd+hYuQk5STj4mCe3Rva2pscHV8g4qQlJWUkIqEfHVva2prbnR7gomPlJWVkYyFfXZwa2lqbXJ5gYiPlJaVk42Gf3dxbGlpbHF4f4eOk5aWlI+IgHhxbGlpa3B2foaNk5aXlZCJgnpybWloam51fYWMkpaXlZGLg3tzbWloaW1ze4OLkZaXlpKMhX11bmpoaWxyeoKKkJWXl5OOhn52b2poaGtxeICIj5WXl5SPiH93cGtoaGpwd3+HjpSXl5WQiYF5cWxoaGpvdX2FjZOWl5WRioJ6c21paGludHyEjJKWl5aSjIR8dG5qaGltc3qCipGVl5aSjYV9dm9qaGlscnmBiY+UlpaTjod/d3BraWlscXh/h46TlpaTjoiAeXJsamlrcHd+ho2SlZaUj4mCenNuamprcHZ9hIuRlJWUkIqDe3Vva2prb3V8g4qPk5WUkIuEfXZwbGtsb3R6gYiOkpSTkIuFfnhybmxsb3N5gIeNkZOTkIyGgHlzb21tb3N4f4WLj5KSkIyHgXp1cG5tb3N4foSKjpGRkIyIgnx2cm9ub3J3fYOIjZCQj4yIg313c3BvcHN3fIKHi46Pj4yIg355dHFwcXN3e4CGio2OjoyJhH96dnNxcXN2e3+EiIuNjYyJhYB7d3RzcnR3en+Dh4qMjIuIhYF8eXZ0c3R3en6ChomKi4qIhYF9end1dXV3en6BhYeJiomIhYJ+e3h3dnZ4en2Ag4aIiYiHhYJ/fHp4d3d5en2AgoWGh4eGhIJ/fXt5eHl5e31/goSFhoaFhIKAfnx7enp6fH1/gYKEhIWEg4KAfn18e3t7fH1/gIKDg4ODgoGAf359fXx9fX5/gIGBgoKCgYGAf39+fn5+fn9/gICAgYGAgICAf39/f39/f39/",
};

export const PlaybackControls = {
  render: () => <main className="public-archive">
    <h1>아카이브 재생</h1>
    <MediaPlayer file={playbackDemo} playbackIdentity="archive-story-playback" sharedModeButtons />
  </main>,
};
