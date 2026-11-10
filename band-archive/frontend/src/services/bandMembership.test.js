import assert from 'node:assert/strict';
import test from 'node:test';
import { isActiveBandMembership } from './bandMembership.js';
import { activeBandsOnly } from '../context/bandSelection.js';
import { activeAvailabilityMember } from '../components/workspace/availabilityState.js';

test('band membership activity uses one explicit lifecycle rule across selection and availability', () => {
  for (const [membership, active] of [
    [undefined, false], [null, false], [{}, true],
    [{ status: 'active' }, true], [{ status: 'ACTIVE' }, true], [{ status: 'AcTiVe' }, true],
    ...['pending', 'left', 'rejected', 'removed', 'suspended', '', 'unknown', ' active ', null, 1].map((status) => [{ status }, false]),
  ]) {
    const band = { id: 1, membership };
    assert.equal(isActiveBandMembership(membership), active, JSON.stringify(membership));
    assert.equal(activeAvailabilityMember(band), active);
    assert.equal(activeBandsOnly([band]).length, active ? 1 : 0);
  }
});
