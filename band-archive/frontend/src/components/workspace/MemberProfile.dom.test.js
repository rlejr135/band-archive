import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {};
dom.window.HTMLElement.prototype.detachEvent = () => {};
let vite; let module;
const load = async () => { if (module) return; vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } }); module = await vite.ssrLoadModule('/src/components/workspace/MemberProfile.jsx'); };
after(async () => { await vite?.close(); dom.window.close(); });
afterEach(() => cleanup());
const profile = (overrides = {}) => ({ id: 4, name: '기덕', biography: '', positions: [], custom_position: '', profile_revision: 0, is_self: true, can_edit_profile: true, ...overrides });
const user = userEvent.setup({ document: dom.window.document });
const editor = (api, props = {}) => createElement(module.MemberProfileEditor, { band: { id: 8 }, api, onSaved: () => {}, onCancel: () => {}, ...props });

test('own profile editor loads authenticated me endpoint and saves only biography, multiple positions and revision', async () => {
  await load(); const calls = []; let saved;
  const api = { requestJson: async (path, options) => { calls.push({ path, options }); return { member: profile(options.method ? { ...JSON.parse(options.body), profile_revision: 1 } : {}) }; } };
  const view = render(editor(api, { onSaved: (member) => { saved = member; } }));
  await view.findByLabelText('간단한 소개');
  assert.equal(calls[0].path, '/api/v2/bands/8/members/me'); assert.equal(calls[0].options.authenticated, undefined);
  assert.ok(calls[0].options.signal); assert.match(view.container.textContent, /비로그인 방문자/);
  assert.match(view.container.textContent, /가입 심사 때 작성한 소개와는 별개/);
  await userEvent.type(view.getByLabelText('간단한 소개'), '<b>함께 합주해요</b>');
  await userEvent.click(view.getByRole('checkbox', { name: '보컬' }));
  await userEvent.click(view.getByRole('checkbox', { name: '기타', exact: true }));
  await userEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  await waitFor(() => assert.ok(saved));
  assert.deepEqual(JSON.parse(calls[1].options.body), { biography: '<b>함께 합주해요</b>', positions: ['VOCAL', 'GUITAR'], profile_revision: 0 });
  assert.equal(calls[1].options.method, 'PATCH'); assert.equal(calls[1].options.headers['Content-Type'], 'application/json');
});

test('band-local name loads from the member profile and saves its normalized value with the revision', async () => {
  await load(); const writes = []; let saved;
  const api = { requestJson: async (_path, options) => { if (options.method) { const body = JSON.parse(options.body); writes.push(body); return { member: profile({ ...body, name: body.name || '기덕', profile_revision: 1 }) }; } return { member: profile({ name: '기덕' }) }; } };
  const view = render(editor(api, { onSaved: (member) => { saved = member; } }));
  const name = await view.findByLabelText('밴드에서 사용할 이름'); assert.equal(name.value, '기덕'); assert.equal(name.required, true); assert.equal(name.maxLength, 200);
  await user.click(name); await user.clear(name); await user.type(name, '  기타이름  ');
  await user.click(view.getByRole('button', { name: '저장', exact: true })); await waitFor(() => assert.ok(saved));
  assert.deepEqual(writes[0], { name: '기타이름', profile_revision: 0 }); assert.equal(saved.name, '기타이름');
});

test('band-local name rejects whitespace and invisible characters, recovers, and enforces the 100-character boundary', async () => {
  await load(); let writes = 0; let payload;
  const api = { requestJson: async (_path, options) => { if (options.method) { writes += 1; payload = JSON.parse(options.body); return { member: profile({ profile_revision: 1 }) }; } return { member: profile() }; } };
  const view = render(editor(api)); const name = await view.findByLabelText('밴드에서 사용할 이름');
  await user.click(name); await user.clear(name); await user.type(name, '   ');
  assert.equal(name.value, '   '); assert.equal(name.getAttribute('aria-invalid'), 'true'); assert.match(view.getByRole('alert').textContent, /공백만으로 저장할 수 없습니다/);
  assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true);
  fireEvent.submit(view.container.querySelector('form')); assert.equal(writes, 0);

  await user.clear(name); await user.type(name, '\u200b');
  assert.equal(name.value, '\u200b'); assert.equal(name.getAttribute('aria-invalid'), 'true'); assert.match(view.getByRole('alert').textContent, /표시할 수 없는 문자/);
  fireEvent.submit(view.container.querySelector('form')); assert.equal(writes, 0);

  await user.clear(name); await user.type(name, 'x'.repeat(101));
  assert.equal(name.value.length, 101); assert.equal(name.getAttribute('aria-invalid'), 'true'); assert.match(view.getByRole('alert').textContent, /100자 이내/);
  assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true);
  fireEvent.submit(view.container.querySelector('form')); assert.equal(writes, 0);
  await user.clear(name); await user.type(name, 'x'.repeat(100));
  assert.equal(name.value.length, 100); assert.equal(name.getAttribute('aria-invalid'), 'false');
  assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, false);
  await user.click(view.getByRole('button', { name: '저장', exact: true }));
  await waitFor(() => assert.equal(writes, 1)); assert.deepEqual(payload, { name: 'x'.repeat(100), profile_revision: 0 });
});

test('band-local name accepts 100 supplementary Unicode characters within the UTF-16 input ceiling', async () => {
  await load(); let payload;
  const api = { requestJson: async (_path, options) => { if (options.method) payload = JSON.parse(options.body); return { member: profile(options.method ? { ...payload, profile_revision: 1 } : {}) }; } };
  const view = render(editor(api)); const name = await view.findByLabelText('밴드에서 사용할 이름'); const hundredEmoji = '😀'.repeat(100);
  assert.equal(name.maxLength, 200);
  await user.clear(name); await user.type(name, hundredEmoji);
  assert.equal(Array.from(name.value).length, 100); assert.equal(name.value.length, 200); assert.equal(name.getAttribute('aria-invalid'), 'false');
  assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, false);
  await user.click(view.getByRole('button', { name: '저장', exact: true })); await waitFor(() => assert.ok(payload));
  assert.equal(payload.name, hundredEmoji); assert.equal(payload.profile_revision, 0);
});

test('optional profile fields may stay blank and custom text clears when OTHER is deselected', async () => {
  await load(); const writes = [];
  const api = { requestJson: async (_path, options) => { if (options.method) writes.push(JSON.parse(options.body)); return { member: profile() }; } };
  const view = render(editor(api)); await view.findByLabelText('간단한 소개');
  assert.equal(view.getByLabelText('간단한 소개').maxLength, 300);
  await userEvent.click(view.getByRole('checkbox', { name: '기타 악기·역할', exact: true }));
  const custom = view.getByLabelText('기타 악기·역할 직접 입력'); assert.equal(custom.maxLength, 50);
  await userEvent.type(custom, '색소폰');
  await userEvent.click(view.getByRole('checkbox', { name: '기타 악기·역할', exact: true }));
  assert.equal(view.queryByLabelText('기타 악기·역할 직접 입력'), null);
  await userEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  await waitFor(() => assert.equal(writes.length, 1));
  assert.deepEqual(writes[0], { biography: '', profile_revision: 0 });
});

test('409 prevents stale resubmit until explicit latest-profile read replaces the draft', async () => {
  await load(); let reads = 0; let writes = 0;
  const api = { requestJson: async (_path, options) => {
    if (options.method) { writes += 1; if (writes === 1) throw Object.assign(new Error('Conflict'), { status: 409 }); return { member: profile({ profile_revision: 3 }) }; }
    reads += 1; return { member: profile(reads > 1 ? { biography: '최신 소개', profile_revision: 2 } : {}) };
  } };
  const view = render(editor(api)); await view.findByLabelText('간단한 소개');
  await userEvent.type(view.getByLabelText('간단한 소개'), '오래된 초안');
  await userEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  await view.findByText(/다른 화면에서 프로필이 변경/);
  assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true); assert.equal(writes, 1); assert.equal(reads, 1);
  await userEvent.click(view.getByRole('button', { name: '최신 프로필 다시 불러오기' }));
  await waitFor(() => assert.equal(view.getByLabelText('간단한 소개').value, '최신 소개'));
  assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, false);
});

test('unknown save outcome requires readback, never offers blind retry', async () => {
  await load(); let writes = 0;
  const api = { requestJson: async (_path, options) => { if (options.method) { writes += 1; throw new Error('Network'); } return { member: profile() }; } };
  const view = render(editor(api)); await view.findByLabelText('간단한 소개');
  await userEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  await view.findByText(/저장 결과를 확인하지 못했습니다/);
  assert.equal(writes, 1); assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true);
});

test('unmount aborts profile read and late save completion cannot notify a different workspace', async () => {
  await load(); let resolveRead; let readSignal;
  const first = render(editor({ requestJson: (_path, options) => { readSignal = options.signal; return new Promise((resolve) => { resolveRead = resolve; }); } }));
  first.unmount(); assert.equal(readSignal.aborted, true);
  await act(async () => resolveRead({ member: profile() }));
  let resolveWrite; let writeSignal; let saves = 0;
  const second = render(editor({ requestJson: async (_path, options) => { if (!options.method) return { member: profile() }; writeSignal = options.signal; return new Promise((resolve) => { resolveWrite = resolve; }); } }, { onSaved: () => { saves += 1; } }));
  await second.findByLabelText('간단한 소개'); await userEvent.click(second.getByRole('button', { name: '저장', exact: true }));
  second.unmount(); assert.equal(writeSignal.aborted, true);
  await act(async () => resolveWrite({ member: profile() })); assert.equal(saves, 0);
});

test('summary displays positions without interpreting biography/custom HTML and preserves legacy instrument', async () => {
  await load();
  const view = render(createElement(module.MemberProfileSummary, { member: profile({ biography: '<img src=x onerror=alert(1)>', positions: ['VOCAL', 'OTHER'], custom_position: '<b>색소폰</b>' }), compact: true }));
  assert.match(view.container.textContent, /보컬/); assert.match(view.container.textContent, /<b>색소폰<\/b>/);
  assert.equal(view.container.querySelector('img,b,script'), null);
  view.rerender(createElement(module.MemberProfileSummary, { member: { instrument: '레거시 악기명' } }));
  assert.match(view.container.textContent, /레거시 악기명/);
});

test('nonself or forbidden profiles never expose editable fields', async () => {
  await load(); const view = render(editor({ requestJson: async () => ({ member: profile({ is_self: false, can_edit_profile: false }) }) }));
  await view.findByText(/내 프로필을 불러오지 못했습니다/);
  assert.equal(view.queryByLabelText('간단한 소개'), null); assert.equal(view.getByRole('button', { name: '저장', exact: true }).disabled, true);
});

test('save rejects duplicate clicks while request remains in flight', async () => {
  await load(); let writes = 0; let resolveWrite;
  const view = render(editor({ requestJson: async (_path, options) => { if (!options.method) return { member: profile() }; writes += 1; return new Promise((resolve) => { resolveWrite = resolve; }); } }));
  await view.findByLabelText('간단한 소개');
  const form = view.container.querySelector('form'); fireEvent.submit(form); fireEvent.submit(form);
  await waitFor(() => assert.equal(writes, 1));
  await act(async () => resolveWrite({ member: profile({ profile_revision: 1 }) }));
});

test('mismatched detail identity and malformed saved profile are rejected without updating another member', async () => {
  await load(); let saves = 0;
  const mismatch = render(editor({ requestJson: async () => ({ member: profile() }) }, { expectedMemberId: 5 }));
  await mismatch.findByText(/내 프로필을 불러오지 못했습니다/); assert.equal(mismatch.queryByLabelText('간단한 소개'), null); mismatch.unmount();
  const view = render(editor({ requestJson: async (_path, options) => ({ member: profile(options.method ? { id: 5, profile_revision: 1 } : {}) }) }, { onSaved: () => { saves += 1; } }));
  await view.findByLabelText('간단한 소개'); await userEvent.click(view.getByRole('button', { name: '저장', exact: true }));
  await view.findByText(/저장 결과를 확인하지 못했습니다/); assert.equal(saves, 0);
});

test('biography-only edit preserves legacy instrument by omitting unchanged structured positions', async () => {
  await load(); let payload;
  const view = render(editor({ requestJson: async (_path, options) => { if (options.method) payload = JSON.parse(options.body); return { member: profile({ instrument: '레거시 현악', ...(options.method ? { ...payload, profile_revision: 1 } : {}) }) }; } }));
  await view.findByText(/기존 포지션 표기: 레거시 현악/); await userEvent.type(view.getByLabelText('간단한 소개'), '소개만 수정');
  await userEvent.click(view.getByRole('button', { name: '저장', exact: true })); await waitFor(() => assert.ok(payload));
  assert.deepEqual(payload, { biography: '소개만 수정', profile_revision: 0 });
});
