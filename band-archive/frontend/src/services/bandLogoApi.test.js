import assert from 'node:assert/strict';
import test from 'node:test';
import { bandLogoManagementPath, bandLogoPayload, bandLogoUpdatePath } from './bandLogoApi.js';

test('logo management path is slug-safe and uses bounded pagination defaults', () => {
  assert.equal(bandLogoManagementPath('private band/2026'), '/api/v2/bands/by-slug/private%20band%2F2026/logo-management?limit=20&offset=0');
  assert.equal(bandLogoManagementPath('band', { limit: 20, offset: 40 }), '/api/v2/bands/by-slug/band/logo-management?limit=20&offset=40');
  assert.equal(bandLogoUpdatePath(41), '/api/v2/bands/41/logo');
});

test('logo update body contains only the selected gallery image id or null', () => {
  assert.deepEqual(bandLogoPayload('42'), { gallery_image_id: 42 });
  assert.deepEqual(bandLogoPayload(null), { gallery_image_id: null });
});
