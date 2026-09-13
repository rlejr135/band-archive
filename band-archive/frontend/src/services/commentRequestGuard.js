export const getCommentTargetKey = (targetType, targetId) => JSON.stringify([targetType || '', String(targetId ?? '')]);

// A request token is scoped to one target and one effect lifecycle. StrictMode
// deliberately performs setup -> cleanup -> setup on the same ref, so a fresh
// lifecycle must reactivate the guard without accepting first-setup results.
export const createCommentRequestGuard = (targetKey) => {
  let lifecycle = 0;
  let activeLifecycle = null;
  let generation = 0;

  return {
    activate: () => { lifecycle += 1; activeLifecycle = lifecycle; return lifecycle; },
    begin: (requestLifecycle = activeLifecycle) => ({ targetKey, lifecycle: requestLifecycle, generation: ++generation }),
    isActive: (requestLifecycle = activeLifecycle) => requestLifecycle !== null && requestLifecycle === activeLifecycle,
    isCurrent: (token) => token?.targetKey === targetKey
      && token.lifecycle !== null
      && token.lifecycle === activeLifecycle
      && token.generation === generation,
    dispose: (requestLifecycle = activeLifecycle) => {
      if (requestLifecycle === activeLifecycle) activeLifecycle = null;
      generation += 1;
    },
  };
};
