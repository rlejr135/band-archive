import assert from 'node:assert/strict';
import test from 'node:test';
import { createBandGalleryUploadTransport, createBandUploadTransport } from './bandUploadTransport.js';
import { galleryUploadProgressPercent, getWebUploadTransport, MULTIPART_THRESHOLD_BYTES, uploadBandGalleryImageFile, uploadBandMediaFile } from './mediaUploadManager.js';
import { createAuthenticatedApiClient } from './authApiClient.js';

test('band upload transport keeps direct and multipart capability requests under its immutable v2 band URL', async () => {
  const calls = [];
  const api = { requestJson: async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith('/direct/initiate')) return { session_id: 'direct-1', upload_capability_token: 'cap-direct', upload_url: 'https://r2/direct' };
    if (path.endsWith('/multipart/initiate')) return { session_id: 'multi-1', upload_capability_token: 'cap-multi' };
    return { ok: true };
  } };
  const transport = createBandUploadTransport({ bandId: 42, api, putPart: async () => 'etag' });
  const direct = await transport.initiateDirect({ filename: 'clip.mp4', declared_bytes: 10, song_id: 7 });
  await transport.completeDirect(direct);
  const multipart = await transport.initiateMultipart({ filename: 'long.mp4', declared_bytes: 100 });
  await transport.requestPart(multipart, 1);
  await transport.acknowledgePart(multipart, 1, { etag: 'etag', bytes: 100 });
  await transport.complete(multipart);
  assert.deepEqual(calls.map((call) => call.path), [
    '/api/v2/bands/42/uploads/direct/initiate',
    '/api/v2/bands/42/uploads/direct/direct-1/complete',
    '/api/v2/bands/42/uploads/multipart/initiate',
    '/api/v2/bands/42/uploads/multipart/multi-1/parts',
    '/api/v2/bands/42/uploads/multipart/multi-1/parts/1/ack',
    '/api/v2/bands/42/uploads/multipart/multi-1/complete',
  ]);
  assert.equal(calls[0].options.headers['X-Upload-Capability'], undefined);
  for (const call of calls.filter((call) => !call.path.endsWith('/initiate'))) assert.equal(call.options.headers['X-Upload-Capability'], call.path.includes('/direct/') ? 'cap-direct' : 'cap-multi');
});

test('small band files use direct presign PUT then authenticated completion with the exact v2 payload', async () => {
  const events = [];
  const transport = {
    initiateDirect: async (payload) => { events.push(['initiate', payload]); return { sessionId: 'd1', capabilityToken: 'cap', upload_url: 'https://r2/direct' }; },
    putPart: async (url, file, contentType, progress) => { events.push(['put', url, contentType]); progress(file.size); },
    completeDirect: async (session) => { events.push(['complete', session.sessionId]); return { media: { id: 9, transcoding_status: 'not_required' } }; },
  };
  const file = { name: 'clip.mp3', type: 'audio/mpeg', size: MULTIPART_THRESHOLD_BYTES - 1 };
  const progress = [];
  const result = await uploadBandMediaFile({ transport, file, target: { kind: 'media', songId: 7 }, onProgress: (loaded, total) => progress.push([loaded, total]) });
  assert.deepEqual(events, [
    ['initiate', { filename: 'clip.mp3', content_type: 'audio/mpeg', declared_bytes: MULTIPART_THRESHOLD_BYTES - 1, song_id: 7, rehearsal_id: undefined }],
    ['put', 'https://r2/direct', 'audio/mpeg'], ['complete', 'd1'],
  ]);
  assert.deepEqual(progress, [[MULTIPART_THRESHOLD_BYTES - 1, MULTIPART_THRESHOLD_BYTES - 1]]);
  assert.equal(result.media.id, 9);
});

test('band initiate refreshes a rejected bearer and retries the same immutable endpoint once', async () => {
  const headers = [];
  const api = createAuthenticatedApiClient({
    apiUrl: 'https://api.example', getAccessToken: async () => 'expired', refreshSession: async () => 'fresh',
    fetchImpl: async (url, options) => {
      headers.push({ url, authorization: options.headers.Authorization });
      return headers.length === 1
        ? { ok: false, status: 401, json: async () => ({}) }
        : { ok: true, status: 201, json: async () => ({ session_id: 'd2', upload_capability_token: 'cap', upload_url: 'https://r2/direct' }) };
    },
  });
  const transport = createBandUploadTransport({ bandId: 5, api, putPart: async () => {} });
  await transport.initiateDirect({ filename: 'refresh.mp3', declared_bytes: 4, song_id: 3 });
  assert.deepEqual(headers, [
    { url: 'https://api.example/api/v2/bands/5/uploads/direct/initiate', authorization: 'Bearer expired' },
    { url: 'https://api.example/api/v2/bands/5/uploads/direct/initiate', authorization: 'Bearer fresh' },
  ]);
});

test('gallery transport captures its v2 band endpoint and capability independently of media uploads', async () => {
  const calls = [];
  const api = { requestJson: async (path, options) => {
    calls.push({ path, options });
    return path.endsWith('/initiate')
      ? { session_id: 'gallery-1', upload_capability_token: 'gallery-cap', upload_url: 'https://r2/gallery' }
      : { gallery: { id: 17 } };
  } };
  const gallery = createBandGalleryUploadTransport({ bandId: 9, api, putPart: async () => {} });
  const session = await gallery.initiate({ filename: 'cover.jpg', declared_bytes: 12 });
  const completed = await gallery.complete(session);
  assert.equal(completed.gallery.id, 17);
  assert.deepEqual(calls.map((call) => call.path), [
    '/api/v2/bands/9/gallery/direct/initiate',
    '/api/v2/bands/9/gallery/direct/gallery-1/complete',
  ]);
  assert.equal(calls[1].options.headers['X-Upload-Capability'], 'gallery-cap');
});

test('gallery FileUpload progress is a clamped percentage, not a byte count', () => {
  const mib = 1024 * 1024;
  assert.equal(galleryUploadProgressPercent(mib, 4 * mib), 25);
  assert.equal(galleryUploadProgressPercent(4 * mib, 4 * mib), 100);
  assert.equal(galleryUploadProgressPercent(5 * mib, 4 * mib), 100);
  assert.equal(galleryUploadProgressPercent(-1, 4 * mib), 0);
  assert.equal(galleryUploadProgressPercent(mib, 0), 0);
});

for (const kind of ['media', 'personal_log']) for (const multipart of [false, true]) {
  test(`confirmed policy stays on ${kind} ${multipart ? 'multipart' : 'direct'} initiate only`, async () => {
    const calls = [];
    const assertion = { version: 'u1', batch_id: '1474c767-d671-4fae-989c-30646802cb63', rights_confirmed: true, visibility: 'MEMBERS' };
    const file = { name: 'clip.mp4', type: 'video/mp4', size: multipart ? MULTIPART_THRESHOLD_BYTES : 12, slice: (start, end) => ({ size: end - start }) };
    const api = { requestJson: async (path, options = {}) => {
      calls.push({ path, body: options.body ? JSON.parse(options.body) : null });
      if (path.endsWith('/initiate')) return { session_id: 'scope', upload_capability_token: 'cap', upload_url: 'https://r2.invalid/direct', part_size: file.size, max_parts: 1 };
      if (path.endsWith('/parts')) return { upload_url: 'https://r2.invalid/part' };
      return path.endsWith('/complete') ? { [kind]: { id: 11 } } : {};
    } };
    const transport = createBandUploadTransport({ bandId: 7, api, putPart: async () => 'etag' });
    await uploadBandMediaFile({ transport, file, uploadPolicy: assertion, target: kind === 'media' ? { kind, songId: 3 } : { kind, memberId: 4 }, onProgress: () => {} });
    assert.deepEqual(calls[0].body.upload_policy, assertion);
    assert.equal(calls[0].body[kind === 'media' ? 'song_id' : 'member_id'], kind === 'media' ? 3 : 4);
    assert.equal(calls.every((call) => call.path.startsWith('/api/v2/bands/7/uploads/')), true);
    assert.deepEqual(calls.at(-1).body, {});
    assert.equal(calls.slice(1).some((call) => call.body?.upload_policy), false);
  });
}

test('gallery carries batch consent at initiate and a stale workspace cannot initiate a new file', async () => {
  const calls = [];
  const assertion = { version: 'u1', batch_id: '1474c767-d671-4fae-989c-30646802cb63', rights_confirmed: true, visibility: 'MEMBERS' };
  const api = { requestJson: async (path, options) => {
    calls.push({ path, body: JSON.parse(options.body) });
    return path.endsWith('/initiate') ? { session_id: 'g', upload_capability_token: 'cap', upload_url: 'https://r2.invalid/gallery' } : { gallery: { id: 12 } };
  } };
  const web = getWebUploadTransport(); const previousPut = web.putPart;
  web.putPart = async () => 'etag';
  try {
    const options = { file: { name: 'photo.jpg', type: 'image/jpeg', size: 12 }, bandId: 7, api, uploadPolicy: assertion };
    await uploadBandGalleryImageFile(options);
    assert.deepEqual(calls[0].body.upload_policy, assertion); assert.deepEqual(calls[1].body, {});
    await assert.rejects(uploadBandGalleryImageFile({ ...options, isCurrent: () => false }), { name: 'AbortError' });
    assert.equal(calls.length, 2);
  } finally { web.putPart = previousPut; }
});
