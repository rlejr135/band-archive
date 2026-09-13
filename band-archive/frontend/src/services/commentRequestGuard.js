export const getCommentTargetKey = (targetType, targetId) => JSON.stringify([targetType || '', String(targetId ?? '')]);

// A request token is scoped to one target and one effect lifecycle. Reads and
// mutations deliberately have separate generations: a POST must not suppress
// an already-running initial read if that POST later fails.
export const createCommentRequestGuard = (targetKey) => {
  let lifecycle = 0;
  let activeLifecycle = null;
  let readGeneration = 0;
  let mutationGeneration = 0;

  return {
    activate: () => { lifecycle += 1; activeLifecycle = lifecycle; return lifecycle; },
    beginRead: (requestLifecycle = activeLifecycle) => ({ targetKey, channel: 'read', lifecycle: requestLifecycle, generation: ++readGeneration }),
    beginMutation: (requestLifecycle = activeLifecycle) => ({ targetKey, channel: 'mutation', lifecycle: requestLifecycle, generation: ++mutationGeneration }),
    isActive: (requestLifecycle = activeLifecycle) => requestLifecycle !== null && requestLifecycle === activeLifecycle,
    isCurrentRead: (token) => token?.targetKey === targetKey
      && token.channel === 'read'
      && token.lifecycle !== null
      && token.lifecycle === activeLifecycle
      && token.generation === readGeneration,
    isCurrentMutation: (token) => token?.targetKey === targetKey
      && token.channel === 'mutation'
      && token.lifecycle !== null
      && token.lifecycle === activeLifecycle
      && token.generation === mutationGeneration,
    dispose: (requestLifecycle = activeLifecycle) => {
      if (requestLifecycle === activeLifecycle) activeLifecycle = null;
    },
  };
};
