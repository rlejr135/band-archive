import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from './AuthContext.jsx';
import { createAuthenticatedApiClient } from '../services/authApiClient.js';
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
  const { getAccessToken, refreshSession } = useAuth();
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [state, setState] = useState(() => ({
    activeBands: [], pendingMemberships: [], bandsLoaded: false, loading: Boolean(userId), error: '',
    selectedBandId: userId ? readSelectedId(userId) : null,
  }));
  const [reloadVersion, setReloadVersion] = useState(0);
  const requestRef = useRef(null);
  const requestGeneration = useRef(0);

  const readMemberships = useCallback(() => {
    if (requestRef.current) return requestRef.current;
    const request = api.requestJson('/api/v2/bands');
    requestRef.current = request;
    const clear = () => { if (requestRef.current === request) requestRef.current = null; };
    request.then(clear, clear);
    return request;
  }, [api]);

  useEffect(() => {
    if (!userId) return undefined;
    let current = true;
    const generation = requestGeneration.current;
    readMemberships().then((response) => {
      if (!current || generation !== requestGeneration.current) return;
      const activeBands = activeBandsOnly(response?.bands);
      const pendingMemberships = Array.isArray(response?.pending_memberships) ? response.pending_memberships : [];
      setState((previous) => {
        const saved = previous.selectedBandId ?? readSelectedId(userId);
        const selected = selectValidBand(saved, activeBands) || activeBands[0] || null;
        writeSelectedId(userId, selected?.id ?? null);
        return { activeBands, pendingMemberships, bandsLoaded: true, loading: false, error: '', selectedBandId: selected?.id ?? null };
      });
    }).catch(() => {
      if (!current || generation !== requestGeneration.current) return;
      setState((previous) => ({ ...previous, bandsLoaded: true, loading: false, error: '밴드 정보를 불러오지 못했습니다. 다시 시도해 주세요.' }));
    });
    return () => { current = false; };
  }, [readMemberships, reloadVersion, userId]);

  const selectBand = useCallback((bandId) => {
    setState((previous) => {
      const selected = selectValidBand(bandId, previous.activeBands);
      writeSelectedId(userId, selected?.id ?? null);
      return { ...previous, selectedBandId: selected?.id ?? null };
    });
  }, [userId]);

  const clearSelectedBand = useCallback(() => {
    writeSelectedId(userId, null);
    setState((previous) => ({ ...previous, selectedBandId: null }));
  }, [userId]);

  const reload = useCallback(() => {
    if (!userId) return;
    // An explicit refresh must observe state after the triggering action,
    // rather than silently joining a read that began before it.
    requestGeneration.current += 1;
    requestRef.current = null;
    setState((previous) => ({ ...previous, activeBands: [], pendingMemberships: [], bandsLoaded: false, loading: true, error: '' }));
    setReloadVersion((version) => version + 1);
  }, [userId]);

  const selectedBand = selectValidBand(state.selectedBandId, state.activeBands);
  const value = useMemo(() => ({
    activeBands: state.activeBands,
    pendingMemberships: state.pendingMemberships,
    bandsLoaded: state.bandsLoaded,
    loading: state.loading,
    error: state.error,
    selectedBand,
    selectBand,
    clearSelectedBand,
    reload,
  }), [clearSelectedBand, reload, selectedBand, selectBand, state.activeBands, state.bandsLoaded, state.error, state.loading, state.pendingMemberships]);
  return <BandContext.Provider value={value}>{children}</BandContext.Provider>;
};

export const BandProvider = ({ children }) => {
  const { user } = useAuth();
  // Remount state on every account boundary; pending requests from the prior
  // identity may finish, but cannot write into the next account's provider.
  return <BandStateProvider key={user?.id ?? 'anonymous'} userId={user?.id ?? null}>{children}</BandStateProvider>;
};

// eslint-disable-next-line react-refresh/only-export-components
export const useBands = () => {
  const context = useContext(BandContext);
  if (!context) throw new Error('useBands must be used inside BandProvider');
  return context;
};
