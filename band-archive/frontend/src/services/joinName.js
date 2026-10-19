// Band-local member name, not the account's global display name.
export const normalizedJoinName = (value) => typeof value === 'string' ? value.trim() : '';
export const validJoinName = (value) => {
  const name = normalizedJoinName(value);
  return name.length > 0 && Array.from(name).length <= 100 && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(value);
};
