import assert from 'node:assert/strict';
import test from 'node:test';
import { reviewPublicVisibility, uploadPolicyConfirmation, uploadPolicyMode } from './uploadPolicy.js';

const config = () => ({ policies: { ready: true, upload: { enabled: true, version: 'u1', default_visibility: 'MEMBERS', notice_url: '/upload-policy' } } });
test('upload policy is enabled only for a ready explicit supported contract', () => {
  assert.equal(uploadPolicyMode(null), 'unavailable'); assert.equal(uploadPolicyMode({ policies: { upload: { enabled: false } } }), 'disabled'); assert.equal(uploadPolicyMode(config()), 'enabled');
  for (const change of [{ enabled: 'true' }, { version: '' }, { default_visibility: 'PUBLIC' }, { notice_url: 'https://other.invalid/' }]) assert.equal(uploadPolicyMode({ policies: { ready: true, upload: { ...config().policies.upload, ...change } } }), 'unavailable');
  assert.equal(uploadPolicyMode({ ...config(), policies: { ...config().policies, ready: false } }), 'unavailable');
  assert.deepEqual(uploadPolicyConfirmation(config(), 'batch', 'MEMBERS'), { version: 'u1', batch_id: 'batch', rights_confirmed: true, visibility: 'MEMBERS' });
});
test('public scope and parent relation changes require an actual explicit confirmation, never an automatic assertion', () => {
  const previous = globalThis.window; const messages = []; globalThis.window = { confirm: (message) => { messages.push(message); return false; } };
  try {
    assert.deepEqual(reviewPublicVisibility(config(), { previous: 'MEMBERS', next: 'PUBLIC' }), { proceed: false });
    assert.deepEqual(reviewPublicVisibility(config(), { previous: 'PUBLIC', next: 'PUBLIC' }), { proceed: true });
    assert.deepEqual(reviewPublicVisibility(config(), { previous: 'MEMBERS', next: 'MEMBERS', relationshipsChanged: true }), { proceed: false });
    assert.match(messages[1], /연결된 곡/); globalThis.window.confirm = () => true;
    assert.deepEqual(reviewPublicVisibility(config(), { previous: 'MEMBERS', next: 'PUBLIC' }), { proceed: true, confirmation: { version: 'u1', confirmed: true } });
    assert.deepEqual(reviewPublicVisibility({ policies: { upload: { enabled: false } } }, { previous: 'MEMBERS', next: 'PUBLIC' }), { proceed: true });
    assert.deepEqual(reviewPublicVisibility(null, { previous: 'MEMBERS', next: 'MEMBERS' }), { proceed: true });
    const unavailable = reviewPublicVisibility(null, { previous: 'MEMBERS', next: 'PUBLIC' });
    assert.equal(unavailable.proceed, false); assert.match(unavailable.error, /설정/);
  } finally { globalThis.window = previous; }
});
