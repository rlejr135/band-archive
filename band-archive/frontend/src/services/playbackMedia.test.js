import assert from 'node:assert/strict';
import test from 'node:test';
import { workspaceMediaFile } from './playbackMedia.js';

test('v2 media and personal logs share source adaptation without inventing audio from processing status', () => {
  const video = { id: 7, file_type: 'video', title: 'Personal practice', playback: { video_url: 'https://media.invalid/video.mp4' } };
  const pending = workspaceMediaFile(video, { status: 'completed', audio_url: 'https://media.invalid/not-a-playback-response.m4a' });
  assert.equal(pending.name, 'Personal practice'); assert.equal(pending.audio_url, null);
  assert.equal(pending.url, 'https://media.invalid/video.mp4');
  const ready = workspaceMediaFile({ ...video, playback: { audio_url: 'https://media.invalid/audio.m4a' } });
  assert.equal(ready.url, null); assert.equal(ready.audio_url, 'https://media.invalid/audio.m4a'); assert.equal(ready.transcoding_status, 'completed');
  assert.equal(workspaceMediaFile({ id: 8, file_type: 'audio', playback: { url: 'https://media.invalid/original.m4a' } }).url, 'https://media.invalid/original.m4a');
  assert.equal(workspaceMediaFile({ ...video, playback: { video_url: 'javascript:alert(1)', audio_url: 'data:audio/mp3;base64,AA' } }).audio_url, null);
});
