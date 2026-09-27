export const createSubmissionGuard = () => {
  let active = false;
  return {
    tryAcquire: () => {
      if (active) return false;
      active = true;
      return true;
    },
    release: () => { active = false; },
    isActive: () => active,
  };
};
