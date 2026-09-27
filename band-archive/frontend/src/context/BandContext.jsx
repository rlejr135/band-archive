import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { useAuth } from './AuthContext.jsx';
import { activeBandsOnly, selectValidBand } from './bandSelection.js';

// eslint-disable-next-line react-refresh/only-export-components
export const BandContext = createContext(null);
const storageKey = (userId) => `band-archive.selected-band:${userId}`;

const readSelectedId = (userId) => {
  try { return Number(window.localStorage.getItem(storageKey(userId))) || null; } catch { return null; }
};
const writeSelectedId = (userId, id) => {
  try {
    if (id) window.localStorage.setItem(storageKey(userId), String(id));
    else window.localStorage.removeItem(storageKey(userId));
  } catch {
    // Storage can be disabled; selection remains available for this session.
  }
};

const BandStateProvider = ({ children, userId }) => {
  const [state, setState] = useState({ userId: null, activeBands: [], bandsLoaded: false, selectedBandId: null });
  // Previous-account data is never exposed while this account is loading.
  const scopedState = state.userId === userId ? state : { userId, activeBands: [], bandsLoaded: false, selectedBandId: null };

  const setAvailableBands = useCallback((bands) => {
    const activeBands = activeBandsOnly(bands);
    setState((previous) => {
      const candidate = previous.userId === userId ? previous.selectedBandId ?? readSelectedId(userId) : readSelectedId(userId);
      const selected = selectValidBand(candidate, activeBands);
      writeSelectedId(userId, selected?.id ?? null);
      return { userId, activeBands, bandsLoaded: true, selectedBandId: selected?.id ?? null };
    });
  }, [userId]);

  const selectBand = useCallback((bandId) => {
    setState((previous) => {
      if (!userId || previous.userId !== userId) return { userId, activeBands: [], bandsLoaded: false, selectedBandId: null };
      const selected = selectValidBand(bandId, previous.activeBands);
      writeSelectedId(userId, selected?.id ?? null);
      return { ...previous, selectedBandId: selected?.id ?? null };
    });
  }, [userId]);

  const clearSelectedBand = useCallback(() => {
    setState((previous) => ({ ...previous, selectedBandId: null }));
  }, []);

  const selectedBand = selectValidBand(scopedState.selectedBandId, scopedState.activeBands);
  const value = useMemo(() => ({
    activeBands: scopedState.activeBands,
    bandsLoaded: scopedState.bandsLoaded,
    selectedBand,
    setAvailableBands,
    selectBand,
    clearSelectedBand,
  }), [clearSelectedBand, scopedState.activeBands, scopedState.bandsLoaded, selectBand, selectedBand, setAvailableBands]);
  return <BandContext.Provider value={value}>{children}</BandContext.Provider>;
};

export const BandProvider = ({ children }) => {
  const { user } = useAuth();
  // Remount state on every account boundary. A previous account's fetched
  // memberships cannot briefly appear while the new account is loading.
  return <BandStateProvider key={user?.id ?? 'anonymous'} userId={user?.id ?? null}>{children}</BandStateProvider>;
};

// eslint-disable-next-line react-refresh/only-export-components
export const useBands = () => {
  const context = useContext(BandContext);
  if (!context) throw new Error('useBands must be used inside BandProvider');
  return context;
};
