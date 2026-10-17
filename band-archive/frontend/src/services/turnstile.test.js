import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';

test('Turnstile script load is shared, excludes secrets, and can retry after network failure', async () => {
  const dom = new JSDOM('<!doctype html><head></head>'); const previousWindow = globalThis.window; const previousDocument = globalThis.document;
  Object.assign(globalThis, { window: dom.window, document: dom.window.document });
  try {
    const { loadTurnstile } = await import('./turnstile.js?network-test');
    const first = loadTurnstile(); assert.equal(loadTurnstile(), first);
    const script = document.querySelector('script'); assert.equal(script.src, 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit');
    script.dispatchEvent(new window.Event('error')); await assert.rejects(first); assert.equal(document.querySelector('script'), null);
    const retry = loadTurnstile(); const replacement = document.querySelector('script'); assert.notEqual(replacement, script);
    const api = {}; window.turnstile = api; replacement.dispatchEvent(new window.Event('load')); assert.equal(await retry, api);
  } finally { dom.window.close(); globalThis.window = previousWindow; globalThis.document = previousDocument; }
});

test('Turnstile script timeout does not cache rejection or let stale load callbacks corrupt a retry', async () => {
  const dom = new JSDOM('<!doctype html><head></head>'); const previousWindow = globalThis.window; const previousDocument = globalThis.document;
  Object.assign(globalThis, { window: dom.window, document: dom.window.document });
  try {
    let timeout; window.setTimeout = (callback) => { timeout = callback; return 1; }; window.clearTimeout = () => {};
    const { loadTurnstile } = await import('./turnstile.js?timeout-test'); const first = loadTurnstile(); const oldScript = document.querySelector('script');
    timeout(); await assert.rejects(first, /captcha_timeout/);
    const retry = loadTurnstile(); oldScript.dispatchEvent(new window.Event('load')); assert.equal(loadTurnstile(), retry);
    const api = {}; window.turnstile = api; document.querySelector('script').dispatchEvent(new window.Event('load')); assert.equal(await retry, api);
  } finally { dom.window.close(); globalThis.window = previousWindow; globalThis.document = previousDocument; }
});
