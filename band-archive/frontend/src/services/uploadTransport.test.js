import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createTransportCache, createWebUploadTransport, isNativeDurableVideo, mapUploadState, resolveUploadTransport } from './uploadTransport.js';

const response = (body = {}, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

test('web multipart requests send the capability header and ACK before complete', async () => {
  const calls = [];
  const transport = createWebUploadTransport({ apiUrl: 'https://api.example', fetchImpl: async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith('/initiate')) return response({ session_id: 's1', upload_capability_token: 'cap', part_size: 10, max_parts: 2 });
    if (url.endsWith('/parts')) return response({ upload_url: 'https://r2.example/part' });
    return response({ status: 'ok' });
  } });
  const session = await transport.initiateMultipart({ filename: 'a.mp4' });
  await transport.getSession(session);
  await transport.requestPart(session, 1);
  await transport.acknowledgePart(session, 1, { etag: 'etag', bytes: 10 });
  await transport.complete(session);
  assert.equal(calls[0].options.headers['X-Upload-Capability'], undefined);
  for (const call of calls.slice(1)) assert.equal(call.options.headers['X-Upload-Capability'], 'cap');
  assert.ok(calls.findIndex((call) => call.url.endsWith('/ack')) < calls.findIndex((call) => call.url.endsWith('/complete')));
});

test('part URL requests are safely repeatable after an expired URL', async () => {
  let partRequests = 0;
  const transport = createWebUploadTransport({ apiUrl: 'https://api.example', fetchImpl: async (url) => {
    if (url.endsWith('/parts')) { partRequests += 1; return response({ upload_url: `https://r2.example/${partRequests}` }); }
    return response({});
  } });
  const session = { sessionId: 's1', capabilityToken: 'cap' };
  const first = await transport.requestPart(session, 1);
  const replacement = await transport.requestPart(session, 1);
  assert.equal(first.upload_url, 'https://r2.example/1');
  assert.equal(replacement.upload_url, 'https://r2.example/2');
});

test('native absence falls back to web and queue states are normalized', () => {
  const transport = resolveUploadTransport({ apiUrl: 'https://api.example' });
  assert.equal(transport.kind, 'web');
  assert.equal(transport.supportsBackground, false);
  assert.equal(mapUploadState('pending'), 'queued');
  assert.equal(mapUploadState('aborted'), 'cancelled');
  assert.equal(mapUploadState('unknown'), 'failed');
});

test('transport cache keeps identity until explicit invalidation', () => {
  let created = 0;
  const cache = createTransportCache(() => ({ id: ++created }));
  assert.strictEqual(cache.get(), cache.get());
  assert.equal(created, 1);
  cache.invalidate();
  assert.equal(cache.get().id, 2);
});

test('iOS durable video snapshots accept MIME values and the legacy public.movie type', () => {
  assert.equal(isNativeDurableVideo({ id: 'one', uri: 'file:///one', mimeType: 'video/quicktime', nativeVideo: true }), true);
  assert.equal(isNativeDurableVideo({ id: 'two', uri: 'file:///two', mimeType: 'public.movie' }), true);
  assert.equal(isNativeDurableVideo({ id: 'three', uri: 'file:///three', mimeType: 'audio/mpeg' }), false);
});

test('iOS uses Capacitor 8 bridge-instance registration instead of the removed static API', async () => {
  const bridge = await readFile(new URL('../../ios/App/App/BridgeViewController.swift', import.meta.url), 'utf8');
  const appDelegate = await readFile(new URL('../../ios/App/App/AppDelegate.swift', import.meta.url), 'utf8');
  assert.match(bridge, /bridge\.registerPluginInstance\(BackgroundUploadPlugin\(\)\)/);
  assert.doesNotMatch(appDelegate, /CAPBridgeViewController\.registerPlugin/);
});
