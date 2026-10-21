const nonEmpty = (value) => typeof value === 'string' && value.trim().length > 0;
export const validateServicePolicy = (config) => {
  const policies = config?.policies; const captcha = config?.captcha; const service = config?.service;
  if (!nonEmpty(service?.name) || !nonEmpty(service?.contact_email) || !nonEmpty(service?.sender_email)
    || typeof policies?.ready !== 'boolean' || typeof policies?.policy_acceptance_enabled !== 'boolean'
    || typeof captcha?.enabled !== 'boolean') throw new Error('invalid_service_policy');
  if (policies.policy_acceptance_enabled && !policies.ready) throw new Error('policy_not_ready');
  if (policies.ready && (!nonEmpty(service.operator_name) || !nonEmpty(policies.terms_version) || !nonEmpty(policies.privacy_version)
    || !nonEmpty(policies.retention_notice) || !nonEmpty(policies.providers_notice))) throw new Error('policy_incomplete');
  if (captcha.enabled && !nonEmpty(captcha.site_key)) throw new Error('captcha_not_ready');
  if (policies.upload !== undefined) {
    const upload = policies.upload;
    if (typeof upload?.enabled !== 'boolean') throw new Error('invalid_upload_policy');
    if (upload.enabled && (!policies.ready || !nonEmpty(upload.version) || upload.default_visibility !== 'MEMBERS' || upload.notice_url !== '/upload-policy')) throw new Error('upload_policy_not_ready');
  }
  return config;
};
