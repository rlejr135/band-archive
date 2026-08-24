export const UPLOAD_QUEUE_STATES = Object.freeze([
  'preparing', 'queued', 'uploading', 'retry_wait', 'completing', 'processing', 'completed', 'failed', 'cancelled',
]);

export const mapUploadState = (value) => {
  if (UPLOAD_QUEUE_STATES.includes(value)) return value;
  if (value === 'pending') return 'queued';
  if (value === 'processing') return 'processing';
  if (value === 'aborted') return 'cancelled';
  return 'failed';
};

export class UploadTransportError extends Error {
  constructor(message, { status, retryable = false } = {}) {
    super(message);
    this.name = 'UploadTransportError';
    this.status = status;
    this.retryable = retryable;
  }
}

const retryableStatus = (status) => status === 401 || status === 403 || status === 408 || status === 429 || status >= 500;

export const createWebUploadTransport = ({ apiUrl, fetchImpl = fetch, xhrFactory = () => new XMLHttpRequest() } = {}) => {
  const requestJson = async (path, { method = 'GET', body, capabilityToken } = {}) => {
    const headers = { 'Content-Type': 'application/json' };
    if (capabilityToken) headers['X-Upload-Capability'] = capabilityToken;
    let response;
    try {
      response = await fetchImpl(`${apiUrl}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
      throw new UploadTransportError('네트워크 연결을 확인한 뒤 다시 시도하세요.', { retryable: true });
    }
    if (!response.ok) {
      let data;
      try { data = await response.json(); } catch { data = null; }
      throw new UploadTransportError(data?.error || data?.message || `요청에 실패했습니다 (${response.status}).`, { status: response.status, retryable: retryableStatus(response.status) });
    }
    return response.json();
  };

  const putPart = (url, blob, contentType, onProgress, registerXhr) => new Promise((resolve, reject) => {
    const xhr = xhrFactory();
    registerXhr?.(xhr);
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(event.loaded); };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.getResponseHeader('ETag')?.replaceAll('"', '') || null);
      else reject(new UploadTransportError(`저장소 업로드에 실패했습니다 (${xhr.status}).`, { status: xhr.status, retryable: retryableStatus(xhr.status) }));
    };
    xhr.onerror = () => reject(new UploadTransportError('저장소 네트워크 오류가 발생했습니다.', { retryable: true }));
    xhr.onabort = () => reject(new DOMException('업로드가 취소되었습니다.', 'AbortError'));
    xhr.open('PUT', url); xhr.setRequestHeader('Content-Type', contentType); xhr.send(blob);
  });

  return {
    kind: 'web',
    supportsBackground: false,
    initiateMultipart: async (payload) => {
      const response = await requestJson('/uploads/multipart/initiate', { method: 'POST', body: payload });
      return { ...response, sessionId: response.session_id, capabilityToken: response.upload_capability_token };
    },
    getSession: (session) => requestJson(`/uploads/multipart/${session.sessionId}`, { capabilityToken: session.capabilityToken }),
    requestPart: (session, partNumber) => requestJson(`/uploads/multipart/${session.sessionId}/parts`, { method: 'POST', capabilityToken: session.capabilityToken, body: { part_number: partNumber } }),
    acknowledgePart: (session, partNumber, payload) => requestJson(`/uploads/multipart/${session.sessionId}/parts/${partNumber}/ack`, { method: 'POST', capabilityToken: session.capabilityToken, body: payload }),
    complete: (session) => requestJson(`/uploads/multipart/${session.sessionId}/complete`, { method: 'POST', capabilityToken: session.capabilityToken, body: {} }),
    abort: (session) => requestJson(`/uploads/multipart/${session.sessionId}/abort`, { method: 'POST', capabilityToken: session.capabilityToken, body: {} }),
    putPart,
    // Browser File objects are deliberately not persisted: reloading requires reselection.
    listPending: async () => [],
    addListener: () => () => {},
  };
};

const BackgroundUpload = registerPlugin('BackgroundUpload');

export const getNativeBackgroundUploadTransport = () => {
  // registerPlugin returns a proxy even in a browser, so platform detection must
  // precede it. This avoids claiming background support where no native plugin exists.
  if (!Capacitor.isNativePlatform() || Capacitor.getPlatform() !== 'android') return null;
  return {
    kind: 'native',
    supportsBackground: true,
    pickFiles: (options) => BackgroundUpload.pickFiles(options),
    requestNotificationPermission: () => BackgroundUpload.requestNotificationPermission(),
    enqueue: (options) => BackgroundUpload.enqueue(options),
    resume: (options = {}) => BackgroundUpload.resume(options),
    cancel: (options) => BackgroundUpload.cancel(options),
    acknowledge: (options) => BackgroundUpload.acknowledge(options),
    syncStatus: (options) => BackgroundUpload.syncProcessingStatus(options),
    listPending: () => BackgroundUpload.listPending(),
    addListener: async (event, listener) => {
      const handle = await BackgroundUpload.addListener(event, listener);
      return () => handle.remove();
    },
  };
};

export const resolveUploadTransport = ({ apiUrl, nativePlugin } = {}) => {
  const web = createWebUploadTransport({ apiUrl });
  const native = nativePlugin || getNativeBackgroundUploadTransport();
  if (!native || typeof native.enqueue !== 'function') return web;
  return {
    ...native,
    kind: 'native',
    supportsBackground: Boolean(native.supportsBackground),
  };
};
import { Capacitor, registerPlugin } from '@capacitor/core';
