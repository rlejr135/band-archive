import assert from 'node:assert/strict';
import test, { after, afterEach } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, Event: dom.window.Event });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.attachEvent = () => {}; dom.window.HTMLElement.prototype.detachEvent = () => {};
const user = userEvent.setup({ document: dom.window.document });
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const policy = (enabled = true) => ({ service: { name: 'Archive', contact_email: 'contact@example.test', operator_name: 'Operator' }, policies: { ready: enabled, terms_version: 't1', privacy_version: 'p1', retention_notice: '검증된 보관 안내', providers_notice: '검증된 공급자 안내', upload: { enabled, version: 'u1', default_visibility: 'MEMBERS', notice_url: '/upload-policy' } } });
const band = { id: 7, name: '들뜬', slug: 'sample', membership: { status: 'active' } };
const file = (name = 'recording.mp4') => new dom.window.File(['video'], name, { type: 'video/mp4' });
let vite; let upload; let auth; let service; let input; let pages; let workspace; let songScreen; let rehearsalScreen; let bands;
const load = async () => {
  if (input) return;
  vite = await createServer({ envDir: false, server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
  input = await vite.ssrLoadModule('/src/components/common/FileUpload.jsx'); auth = await vite.ssrLoadModule('/src/context/AuthContext.jsx'); upload = await vite.ssrLoadModule('/src/context/UploadContext.jsx'); service = await vite.ssrLoadModule('/src/context/ServicePolicyContext.jsx'); pages = await vite.ssrLoadModule('/src/components/auth/PolicyPages.jsx');
  workspace = await vite.ssrLoadModule('/src/components/workspace/BandWorkspace.jsx'); songScreen = await vite.ssrLoadModule('/src/components/workspace/songs/SongWorkspace.jsx'); rehearsalScreen = await vite.ssrLoadModule('/src/components/workspace/rehearsals/RehearsalWorkspace.jsx');
  bands = await vite.ssrLoadModule('/src/context/BandContext.jsx');
};
after(async () => { await vite?.close(); dom.window.close(); }); afterEach(() => cleanup());
const transport = { kind: 'web' }; const noop = () => {};
const controller = (onBatch) => ({ uploadBatch: onBatch, nativePending: [], transport, cancel: noop, cancelByNativeId: noop, subscribeMediaCompletion: () => noop });
const app = ({ config = policy(), id = 'u1', generation = 0, targetBand = band, source = controller(async () => []), onUpload, parentVisibility = 'PUBLIC' } = {}) => createElement(auth.AuthContext.Provider, { value: { user: { id }, authGeneration: generation, getAccessToken: async () => id, refreshSession: async () => id } }, createElement(service.ServicePolicyContext.Provider, { value: { config, loading: false, retry: noop } }, createElement(upload.UploadContext.Provider, { value: source }, createElement(MemoryRouter, null, createElement(input.default, { band: targetBand, songId: 12, onUpload, parentVisibility })))));
const choose = (view, files = [file()]) => user.upload(view.container.querySelector('input[type="file"]'), files);
const confirmRights = async (view) => { await user.click(view.getByLabelText('자동 처리·공개 범위·탈퇴 후 콘텐츠 보존·문의 방법 안내를 확인했습니다.')); await user.click(view.getByLabelText('선택한 파일을 이 범위로 게시·공유할 권한이 있고 필요한 출연자·권리자의 허락을 받았습니다.')); };

const workspaceAuth = { user: { id: 'owner' }, getAccessToken: async () => 'test-token', refreshSession: async () => 'test-token', signOut: () => {} };
const workspaceApp = ({ kind = 'songs', config = policy() } = {}) => createElement(service.ServicePolicyContext.Provider, { value: { config, loading: false, error: '', retry: () => {} } },
  createElement(auth.AuthContext.Provider, { value: workspaceAuth }, createElement(bands.BandContext.Provider, { value: { activeBands: [] } },
    createElement(MemoryRouter, { initialEntries: [`/bands/sample/${kind}`] }, createElement(Routes, null,
      createElement(Route, { path: '/bands/:bandSlug', element: createElement(workspace.BandWorkspaceRoute) },
        createElement(Route, { path: kind, element: createElement(kind === 'songs' ? songScreen.SongWorkspace : rehearsalScreen.RehearsalWorkspace) })))))));

test('actual song edit blocks unavailable policy and cancelled public expansion before sending an attestation', async () => {
  await load(); const previous = globalThis.fetch; const previousConfirm = window.confirm; const writes = [];
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/')) return response({ band: { ...band, default_visibility: 'PUBLIC' } });
    if (options.method === 'PATCH') { writes.push(JSON.parse(options.body)); return response({ song: {} }); }
    if (url.includes('/songs?')) return response({ songs: [{ id: 3, title: '비공개 곡', artist: '밴드', visibility: 'MEMBERS', can_edit: true }], pagination: { has_more: false } });
    return response({ media: [], pagination: { has_more: false } });
  };
  try {
    const view = render(workspaceApp({ config: null }));
    await view.findByText('비공개 곡'); await user.click(view.getByText('비공개 곡')); await user.click(view.getByRole('button', { name: '수정' }));
    await user.selectOptions(view.getByLabelText('공개 범위'), 'PUBLIC');
    await user.click(view.getByRole('button', { name: '저장', exact: true }));
    await view.findByText(/현재 정책 설정을 불러오지 못했습니다/); assert.equal(writes.length, 0);
    view.rerender(workspaceApp()); window.confirm = () => false;
    await user.click(view.getByRole('button', { name: '저장', exact: true })); assert.equal(writes.length, 0);
    window.confirm = () => true;
    await user.click(view.getByRole('button', { name: '저장', exact: true }));
    await waitFor(() => assert.equal(writes.length, 1));
    assert.equal(writes[0].visibility, 'PUBLIC'); assert.deepEqual(writes[0].visibility_confirm, { version: 'u1', confirmed: true });
  } finally { globalThis.fetch = previous; window.confirm = previousConfirm; }
});

test('removing a linked song from a private rehearsal requires public-expansion review without changing its visibility', async () => {
  await load(); const previous = globalThis.fetch; const previousConfirm = window.confirm; const writes = []; const prompts = [];
  globalThis.fetch = async (url, options = {}) => {
    if (url.includes('/by-slug/')) return response({ band: { ...band, default_visibility: 'PUBLIC' } });
    if (options.method === 'PATCH') { writes.push(JSON.parse(options.body)); return response({ rehearsal: {} }); }
    if (url.includes('/rehearsals?')) return response({ rehearsals: [{ id: 4, title: '비공개 합주', date: '2026-10-21', visibility: 'MEMBERS', can_edit: true, songs: [{ id: 3, title: '연결된 곡' }] }], pagination: { has_more: false } });
    if (url.includes('/songs?')) return response({ songs: [{ id: 3, title: '연결된 곡', artist: '밴드' }], pagination: { has_more: false } });
    return response({ media: [], pagination: { has_more: false } });
  };
  try {
    const view = render(workspaceApp({ kind: 'rehearsals' }));
    await view.findByText('비공개 합주'); await user.click(view.getByText('비공개 합주')); await user.click(view.getByRole('button', { name: '수정' }));
    await view.findByLabelText(/연결된 곡 —/); await user.click(view.getByLabelText(/연결된 곡 —/));
    window.confirm = (message) => { prompts.push(message); return false; };
    await user.click(view.getByRole('button', { name: '저장', exact: true })); assert.equal(writes.length, 0); assert.match(prompts[0], /연결된 곡/);
    window.confirm = () => true;
    await user.click(view.getByRole('button', { name: '저장', exact: true })); await waitFor(() => assert.equal(writes.length, 1));
    assert.equal(writes[0].visibility, 'MEMBERS'); assert.deepEqual(writes[0].song_ids, []); assert.deepEqual(writes[0].visibility_confirm, { version: 'u1', confirmed: true });
  } finally { globalThis.fetch = previous; window.confirm = previousConfirm; }
});

test('selecting, excluding and cancelling files cannot start a transfer; disabled rollout keeps the old wire contract', async () => {
  await load(); const batches = []; const source = controller(async (batch) => { batches.push(batch); return []; });
  const view = render(app({ config: policy(false), source })); await choose(view, [file('one.mp4'), file('two.mp4')]);
  assert.equal(batches.length, 0); await user.click(view.getByRole('button', { name: 'two.mp4 제외' })); assert.equal(view.queryByText('two.mp4') === null, true);
  await user.click(view.getByRole('button', { name: '선택 취소' })); assert.equal(view.queryByRole('region', { name: '업로드 전 확인' }) === null, true); assert.equal(batches.length, 0);
  await choose(view); await user.click(view.getByRole('button', { name: '업로드 시작' })); await waitFor(() => assert.equal(batches.length, 1)); assert.equal(batches[0].items[0].options.uploadPolicy, undefined);
});

test('one explicit confirmed batch carries immutable policy metadata and defaults to MEMBERS without rewriting the parent', async () => {
  await load(); const previous = globalThis.fetch; const batches = []; const pending = deferred();
  globalThis.fetch = async () => response({ user: { upload_policy_acceptance_required: false } });
  try {
    const view = render(app({ source: controller((batch) => { batches.push(batch); return pending.promise; }), parentVisibility: 'MEMBERS' })); await choose(view, [file('one.mp4'), file('two.mp4')]);
    await waitFor(() => assert.equal(view.queryByText('최초 업로드 확인 상태를 불러오는 중입니다…') === null, true)); assert.equal(batches.length, 0); assert.equal(view.getByRole('button', { name: '업로드 시작' }).disabled, true); assert.equal(view.getByLabelText('공개 범위').value, 'MEMBERS');
    await confirmRights(view); await user.selectOptions(view.getByLabelText('공개 범위'), 'PUBLIC'); assert.equal(view.getByRole('button', { name: '업로드 시작' }).disabled, true); assert.ok(view.getByText(/부모 콘텐츠나 밴드가 멤버 전용/));
    await user.click(view.getByLabelText('선택한 파일을 이 범위로 게시·공유할 권한이 있고 필요한 출연자·권리자의 허락을 받았습니다.')); const start = view.getByRole('button', { name: '업로드 시작' }); await user.dblClick(start);
    assert.equal(batches.length, 1); const assertion = batches[0].items[0].options.uploadPolicy; assert.match(assertion.batch_id, /^[0-9a-f-]{36}$/); assert.deepEqual(assertion, { version: 'u1', batch_id: assertion.batch_id, rights_confirmed: true, visibility: 'PUBLIC' }); assert.deepEqual(batches[0].items[1].options.uploadPolicy, assertion);
    await act(async () => { pending.resolve([]); });
  } finally { globalThis.fetch = previous; }
});

test('existing accounts must confirm current terms and age when the upload receipt is required', async () => {
  await load(); const previous = globalThis.fetch; const calls = []; let batches = 0;
  globalThis.fetch = async (url, options = {}) => { calls.push([String(url), options]); return response(options.method === 'POST' ? { accepted: true } : { user: { upload_policy_acceptance_required: true } }); };
  try {
    const view = render(app({ source: controller(async () => { batches += 1; return []; }) })); await choose(view); await view.findByText('최초 업로드 확인'); await confirmRights(view); assert.equal(view.getByRole('button', { name: '업로드 시작' }).disabled, true);
    await user.click(view.getByLabelText(/이용약관.*동의합니다/)); await user.click(view.getByLabelText(/만 14세 이상입니다/)); await user.click(view.getByRole('button', { name: '업로드 시작' })); await waitFor(() => assert.equal(batches, 1));
    const write = calls.find(([, options]) => options.method === 'POST'); assert.ok(write[0].endsWith('/me/upload-policy-acceptance')); assert.deepEqual(JSON.parse(write[1].body), { terms_version: 't1', privacy_version: 'p1', terms_accepted: true, age_14_plus: true });
  } finally { globalThis.fetch = previous; }
});

test('cancelling a pending first-upload acceptance aborts it and a late receipt starts no upload', async () => {
  await load(); const previous = globalThis.fetch; const pending = deferred(); let signal; let batches = 0;
  globalThis.fetch = async (_url, options = {}) => options.method === 'POST' ? (signal = options.signal, pending.promise) : response({ user: { upload_policy_acceptance_required: true } });
  try {
    const view = render(app({ source: controller(async () => { batches += 1; return []; }) })); await choose(view); await view.findByText('최초 업로드 확인'); await confirmRights(view); await user.click(view.getByLabelText(/이용약관.*동의합니다/)); await user.click(view.getByLabelText(/만 14세 이상입니다/)); await user.click(view.getByRole('button', { name: '업로드 시작' }));
    await waitFor(() => assert.ok(signal)); await user.click(view.getByRole('button', { name: '선택 취소' })); assert.equal(signal.aborted, true); await act(async () => { pending.resolve(response({ accepted: true })); }); assert.equal(batches, 0);
  } finally { globalThis.fetch = previous; }
});

test('account, auth generation and band switches discard staged files before old reads settle', async () => {
  await load(); const previous = globalThis.fetch;
  try {
    for (const change of [{ id: 'u2' }, { generation: 1 }, { targetBand: { ...band, id: 8 } }]) {
      const pending = deferred(); let signal; globalThis.fetch = async (_url, options) => { signal = options.signal; return pending.promise; };
      const view = render(app()); await choose(view); await waitFor(() => assert.ok(signal)); view.rerender(app(change)); assert.equal(view.queryByRole('region', { name: '업로드 전 확인' }) === null, true); assert.equal(signal.aborted, true); await act(async () => { pending.resolve(response({ user: { upload_policy_acceptance_required: false } })); }); assert.equal(view.queryByRole('button', { name: '업로드 시작' }) === null, true); view.unmount();
    }
  } finally { globalThis.fetch = previous; }
});

test('new policy versions reset batch confirmations and malformed receipt status cannot enable start', async () => {
  await load(); const previous = globalThis.fetch; let malformed = false;
  globalThis.fetch = async () => response({ user: malformed ? {} : { upload_policy_acceptance_required: false } });
  try {
    const view = render(app()); await choose(view); await confirmRights(view); await waitFor(() => assert.equal(view.getByRole('button', { name: '업로드 시작' }).disabled, false));
    const next = policy(); next.policies.upload.version = 'u2'; view.rerender(app({ config: next })); assert.equal(view.getByLabelText('선택한 파일을 이 범위로 게시·공유할 권한이 있고 필요한 출연자·권리자의 허락을 받았습니다.').checked, false);
    view.unmount(); malformed = true; const bad = render(app()); await choose(bad); await bad.findByRole('alert'); await confirmRights(bad); assert.equal(bad.getByRole('button', { name: '업로드 시작' }).disabled, true);
  } finally { globalThis.fetch = previous; }
});

test('gallery callback receives the same batch assertion instead of silently bypassing policy', async () => {
  await load(); const previous = globalThis.fetch; const uploads = [];
  globalThis.fetch = async () => response({ user: { upload_policy_acceptance_required: false } });
  try {
    const view = render(app({ onUpload: async (...args) => { uploads.push(args); return { id: 9 }; } })); await choose(view); await confirmRights(view); await waitFor(() => assert.equal(view.getByRole('button', { name: '업로드 시작' }).disabled, false)); await user.click(view.getByRole('button', { name: '업로드 시작' })); await waitFor(() => assert.equal(uploads.length, 1)); assert.equal(uploads[0][2].uploadPolicy.visibility, 'MEMBERS'); assert.equal(uploads[0][2].uploadPolicy.rights_confirmed, true);
  } finally { globalThis.fetch = previous; }
});

test('unpublished policy pages retain the preparation screen and published pages use the central contact and facts', async () => {
  await load(); const show = (kind, config) => createElement(service.ServicePolicyContext.Provider, { value: { config, loading: false } }, createElement(MemoryRouter, null, createElement(pages.PolicyPage, { kind })));
  const view = render(show('upload', policy(false))); assert.ok(view.getByRole('status')); assert.equal(view.queryByText(/저장·백업·압축/) === null, true); view.unmount();
  const published = render(show('terms', policy())); assert.ok(published.getByText(/비독점적 이용허락/)); assert.ok(published.getByText(/홍보·재판매·AI 학습 권한은 포함하지/)); assert.ok(published.getByText(/법정 권리 요청은 이 UI 제한과 별개/)); published.unmount();
  const privacy = render(show('privacy', policy())); assert.ok(privacy.getByText('검증된 보관 안내')); assert.ok(privacy.getByText('검증된 공급자 안내')); assert.ok(privacy.getAllByRole('link', { name: 'contact@example.test' }).every((link) => link.getAttribute('href') === 'mailto:contact@example.test'));
});
