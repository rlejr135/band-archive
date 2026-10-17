import { API_URL } from './api.js';

export class ApiRequestError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
  }
}

const asHeaders = (headers = {}) => ({ ...headers });
const AUTH_REQUIRED_MESSAGE = '로그인이 필요합니다. 다시 로그인해 주세요.';

// Clients are recreated by React as their provider renders.  Key the
// in-flight refresh by the provider callback so concurrent clients sharing
// one AuthProvider also share exactly one SDK refresh.
const refreshFlights = new WeakMap();

const refreshAccessToken = async (refreshSession) => {
  if (typeof refreshSession !== 'function') throw new ApiRequestError(AUTH_REQUIRED_MESSAGE, 401);
  const existing = refreshFlights.get(refreshSession);
  if (existing) return existing;
  const flight = (async () => {
    try {
      const token = await refreshSession();
      if (!token) throw new ApiRequestError(AUTH_REQUIRED_MESSAGE, 401);
      return token;
    } catch (error) {
      if (error instanceof ApiRequestError) throw error;
      throw new ApiRequestError(AUTH_REQUIRED_MESSAGE, 401);
    } finally {
      refreshFlights.delete(refreshSession);
    }
  })();
  refreshFlights.set(refreshSession, flight);
  return flight;
};

export const createAuthenticatedApiClient = ({
  apiUrl = API_URL,
  fetchImpl = fetch,
  getAccessToken,
  refreshSession,
} = {}) => {
  const requestJson = async (path, {
    authenticated = true,
    optionalAuth = false,
    headers = {},
    ...requestOptions
  } = {}, errorMessage = '요청을 처리할 수 없습니다.') => {
    const assertActive = () => { if (requestOptions.signal?.aborted) throw new DOMException('Aborted', 'AbortError'); };
    assertActive();
    const send = async (token) => {
      assertActive();
      const nextHeaders = asHeaders(headers);
      if (authenticated || (optionalAuth && token)) {
        if (authenticated && !token) throw new ApiRequestError(AUTH_REQUIRED_MESSAGE, 401);
        if (token) {
          nextHeaders.Authorization = `Bearer ${token}`;
        }
      }
      return fetchImpl(`${apiUrl}${path}`, { ...requestOptions, headers: nextHeaders });
    };

    const canAuthenticate = authenticated || optionalAuth;
    let token = canAuthenticate ? await getAccessToken?.() : null;
    let response = await send(token);
    assertActive();
    if (canAuthenticate && token && response.status === 401) {
      token = await refreshAccessToken(refreshSession);
      assertActive();
      response = await send(token);
    }
    if (!response.ok) throw new ApiRequestError(errorMessage, response.status);
    return response.json();
  };

  return { requestJson };
};
