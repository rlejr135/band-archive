export const VOTER_ID_STORAGE_KEY = 'band-archive:voter-id:v1';

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export const isValidVoterId = (value) => typeof value === 'string' && UUID_PATTERN.test(value);

const unavailable = (message) => new Error(`투표 식별자를 ${message}. 브라우저 저장소를 확인한 뒤 다시 시도하세요.`);

/**
 * Keeps one opaque, versioned browser identifier. It is only sent as the
 * X-Voter-ID request header and is never rendered or logged by the UI.
 */
export const getVoterId = ({ storage = globalThis.localStorage, cryptoApi = globalThis.crypto } = {}) => {
  if (!storage?.getItem || !storage?.setItem) throw unavailable('저장할 수 없습니다');

  let stored;
  try {
    stored = storage.getItem(VOTER_ID_STORAGE_KEY);
  } catch {
    throw unavailable('읽을 수 없습니다');
  }

  if (isValidVoterId(stored)) return stored.toLowerCase();
  if (!cryptoApi?.randomUUID) throw unavailable('생성할 수 없습니다');

  const created = cryptoApi.randomUUID();
  if (!isValidVoterId(created)) throw unavailable('검증할 수 없습니다');
  try {
    storage.setItem(VOTER_ID_STORAGE_KEY, created.toLowerCase());
  } catch {
    throw unavailable('저장할 수 없습니다');
  }
  return created.toLowerCase();
};
