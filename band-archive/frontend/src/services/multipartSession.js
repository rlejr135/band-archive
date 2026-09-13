const throwIfCancelled = (isCancelled) => {
  if (isCancelled?.()) throw new DOMException('업로드가 취소되었습니다.', 'AbortError');
};

// Session ownership begins as soon as initiate returns. If the user cancelled
// while that request was in flight, abort the just-created server session
// before surfacing AbortError.
export const initiateMultipartSession = async ({ initiate, setSession, isCancelled, abort }) => {
  throwIfCancelled(isCancelled);
  const initiated = await initiate();
  if (!initiated.capabilityToken) throw new Error('업로드 권한 토큰을 받지 못했습니다. 다시 시도하세요.');
  const session = { sessionId: initiated.sessionId, capabilityToken: initiated.capabilityToken };
  setSession?.(session);
  if (isCancelled?.()) {
    await abort?.(session);
    throwIfCancelled(isCancelled);
  }
  return { initiated, session };
};
