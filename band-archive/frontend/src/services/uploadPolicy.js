export const uploadPolicyMode = (config) => {
  if (!config) return 'unavailable';
  const upload = config.policies?.upload;
  if (upload?.enabled === false) return 'disabled';
  const valid = upload?.enabled === true && config.policies.ready === true
    && typeof upload.version === 'string' && upload.version.trim()
    && upload.default_visibility === 'MEMBERS' && upload.notice_url === '/upload-policy';
  if (valid) return 'enabled';
  return 'unavailable';
};

export const uploadPolicyConfirmation = (config, batchId, visibility) => {
  if (uploadPolicyMode(config) !== 'enabled' || !['PUBLIC', 'MEMBERS'].includes(visibility)) {
    throw new Error('업로드 정책을 다시 확인해 주세요.');
  }
  return { version: config.policies.upload.version, batch_id: batchId, rights_confirmed: true, visibility };
};

export const reviewPublicVisibility = (config, { previous, next, relationshipsChanged = false } = {}) => {
  const expandingVisibility = previous != null && previous !== 'PUBLIC' && next === 'PUBLIC';
  if (!expandingVisibility && !relationshipsChanged) return { proceed: true };
  const mode = uploadPolicyMode(config);
  if (mode === 'disabled') return { proceed: true };
  if (mode === 'unavailable') {
    return { proceed: false, error: '공개 범위를 확인할 현재 정책 설정을 불러오지 못했습니다. 새로고침 후 다시 시도해 주세요.' };
  }
  const message = relationshipsChanged
    ? '연결된 곡을 변경하면 기존 미디어의 공개 범위가 넓어져 비회원도 볼 수 있습니다. 필요한 출연자·권리자의 허락을 확인하고 계속할까요?'
    : '전체 공개로 변경하면 비회원도 이 콘텐츠와 공개 가능한 연결 미디어를 볼 수 있습니다. 필요한 출연자·권리자의 허락을 받았는지 확인하고 계속할까요?';
  if (!window.confirm(message)) return { proceed: false };
  return { proceed: true, confirmation: { version: config.policies.upload.version, confirmed: true } };
};
