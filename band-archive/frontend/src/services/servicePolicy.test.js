import assert from 'node:assert/strict';
import test from 'node:test';
import { validateServicePolicy } from './servicePolicy.js';

const valid = () => ({ service: { name: 'Archive', contact_email: 'contact@example.test', sender_email: 'sender@example.test', operator_name: 'Operator' }, policies: { ready: true, policy_acceptance_enabled: true, terms_version: 't1', privacy_version: 'p1', retention_notice: 'retention', providers_notice: 'providers' }, captcha: { enabled: true, site_key: 'public-key' } });
test('validated runtime config retains one authoritative service/contact source', () => {
  const value = valid(); assert.equal(validateServicePolicy(value), value);
  const off = valid(); off.policies = { ready: false, policy_acceptance_enabled: false }; off.captcha = { enabled: false }; assert.equal(validateServicePolicy(off), off);
});
test('missing/malformed flags cannot silently bypass policy or CAPTCHA', () => {
  for (const field of ['ready', 'policy_acceptance_enabled']) for (const value of [undefined, null, 'false', 0]) {
    const config = valid(); config.policies[field] = value; assert.throws(() => validateServicePolicy(config));
  }
  for (const enabled of [undefined, null, 'false', 0]) { const config = valid(); config.captcha.enabled = enabled; assert.throws(() => validateServicePolicy(config)); }
});
test('enabled policy and CAPTCHA reject incomplete facts, versions, and public key', () => {
  for (const field of ['terms_version', 'privacy_version', 'retention_notice', 'providers_notice']) { const config = valid(); config.policies[field] = ''; assert.throws(() => validateServicePolicy(config)); }
  const missingOperator = valid(); missingOperator.service.operator_name = ''; assert.throws(() => validateServicePolicy(missingOperator));
  const notReady = valid(); notReady.policies.ready = false; assert.throws(() => validateServicePolicy(notReady));
  const missingKey = valid(); missingKey.captcha.site_key = ''; assert.throws(() => validateServicePolicy(missingKey));
});
