export const getCommentTargetKey = (targetType, targetId) => JSON.stringify([targetType || '', String(targetId ?? '')]);

// A request token is scoped to exactly one mounted comment target. Starting a
// newer request supersedes older reads, while disposing the guard makes every
// late completion a no-op for React state.
export const createCommentRequestGuard = (targetKey) => {
  let active = true;
  let generation = 0;

  return {
    begin: () => ({ targetKey, generation: ++generation }),
    isActive: () => active,
    isCurrent: (token) => active && token?.targetKey === targetKey && token.generation === generation,
    dispose: () => { active = false; generation += 1; },
  };
};
