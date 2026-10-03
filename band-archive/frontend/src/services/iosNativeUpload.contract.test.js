import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const swift = (name) => readFile(new URL(`../../ios/App/App/BackgroundUpload/${name}`, import.meta.url), 'utf8');

test('iOS v2 queue keeps immutable band scope, Keychain session rotation, and opaque background metadata', async () => {
  const [foundation, engine, plugin, auth, supabase, project] = await Promise.all([
    swift('BackgroundUploadFoundation.swift'), swift('IOSMultipartEngine.swift'), swift('BackgroundUploadPlugin.swift'), swift('SecureAuthSessionStore.swift'),
    readFile(new URL('./supabase.js', import.meta.url), 'utf8'),
    readFile(new URL('../../ios/App/App.xcodeproj/project.pbxproj', import.meta.url), 'utf8'),
  ]);
  assert.match(foundation, /protocolVersion: String = "v1"/);
  assert.match(foundation, /bandID: Int64\?; var bandName: String\?; var bandSlug: String\?; var accountID: String\?/);
  assert.match(foundation, /ALTER TABLE tasks ADD COLUMN protocol/);
  assert.match(foundation, /UPDATE tasks SET protocol='v1',mode='multipart'/);
  assert.match(foundation, /state='auth_required'.*lease_owner=NULL/s);
  assert.ok(engine.includes('/api/v2/bands/\\(task.bandID!)/uploads'));
  assert.ok(engine.includes('/\\(task.mode)/initiate'));
  assert.ok(engine.includes('/direct/\\(sessionID)/complete'));
  assert.match(engine, /X-Upload-Capability/);
  assert.match(engine, /Authorization/);
  assert.match(engine, /error\.status == 401/);
  assert.match(engine, /forceRefresh: true/);
  assert.match(engine, /session\.uploadTask\(with: request, fromFile: source\)/);
  assert.match(plugin, /setSecureAuthSession/);
  assert.match(plugin, /getSecureAuthSession/);
  assert.match(plugin, /removeSecureAuthSession/);
  assert.match(plugin, /refreshCredentials/);
  assert.match(plugin, /pauseAfterLogout/);
  assert.match(auth, /SecItemUpdate/);
  assert.match(auth, /refresh_token/);
  assert.match(auth, /auth\/v1\/token/);
  assert.match(auth, /accountMismatch/);
  assert.match(engine, /filter \{ \$0\.descriptor\.part > 0 \}/);
  assert.match(plugin, /retainedTasks\(accountID:account\)/);
  assert.match(plugin, /engine\.cancel\(id,accountID:currentAccountID\(\)\)/);
  assert.match(plugin, /IOSUploadAccountScope\.allows/);
  assert.match(plugin, /value\["accountId"\]=task\.accountID/);
  assert.match(auth, /mutationLock/);
  assert.match(auth, /inFlight/);
  assert.match(auth, /Self\.generation == snapshot\.generation/);
  assert.match(foundation, /isDurableDirectSource/);
  assert.doesNotMatch(auth, /UserDefaults/);
  assert.match(supabase, /\['android', 'ios'\]/);
  assert.match(supabase, /storage: isNativeSecureSession\(\) \? nativeAuthStorage : window\.localStorage/);
  assert.match(project, /SecureAuthSessionStore\.swift in Sources/);
});
