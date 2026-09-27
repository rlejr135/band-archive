import { API_URL } from './api.js';

export class ApiRequestError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
  }
}

const asHeaders = (headers = {}) => ({ ...headers });

export const createAuthenticatedApiClient = ({
  apiUrl = API_URL,
  fetchImpl = fetch,
  getAccessToken,
  refreshSession,
} = {}) => {
  const requestJson = async (path, {
    authenticated = true,
    headers = {},
    ...requestOptions
  } = {}, errorMessage = '요청을 처리할 수 없습니다.') => {
    const send = async (token) => {
      const nextHeaders = asHeaders(headers);
      if (authenticated) {
        if (!token) throw new ApiRequestError('로그인이 필요합니다.', 401);
        nextHeaders.Authorization = `Bearer ${token}`;
      }
      return fetchImpl(`${apiUrl}${path}`, { ...requestOptions, headers: nextHeaders });
    };

    let token = authenticated ? await getAccessToken?.() : null;
    let response = await send(token);
    if (authenticated && response.status === 401) {
      token = await refreshSession?.();
      response = await send(token);
    }
    if (!response.ok) throw new ApiRequestError(errorMessage, response.status);
    return response.json();
  };

  return { requestJson };
};
