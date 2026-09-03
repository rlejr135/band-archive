import assert from 'node:assert/strict';
import test from 'node:test';
import { getUploadStatusLabel, isUploadCancellable, isUploadTerminal } from './uploadPresentation.js';

test('upload presentation keeps every shared status label and progress format stable', () => {
  assert.equal(getUploadStatusLabel('pending'), '준비');
  assert.equal(getUploadStatusLabel('uploading', 42), '업로드 42%');
  assert.equal(getUploadStatusLabel('completed', 0, 'file'), '음원 추출 완료');
  assert.equal(getUploadStatusLabel('processing'), '음원 추출 중');
  assert.equal(getUploadStatusLabel('completed'), '완료');
});

test('upload presentation distinguishes cancellable and terminal states', () => {
  assert.equal(isUploadCancellable('processing'), true);
  assert.equal(isUploadCancellable('completed'), false);
  assert.equal(isUploadTerminal('failed'), true);
  assert.equal(isUploadTerminal('uploading'), false);
});
