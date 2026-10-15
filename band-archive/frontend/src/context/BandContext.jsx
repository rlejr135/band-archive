import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from './AuthContext.jsx';
import { createAuthenticatedApiClient } from '../services/authApiClient.js';
import { activeBandsOnly, selectValidBand } from './bandSelection.js';

// eslint-disable-next-line react-refresh/only-export-components
export const BandContext = createContext(null);
const storageKey = (userId) => `band-archive.selected-band:${userId}`;
const membershipSignalKey = (userId) => `band-archive.membership-change:${userId}`;

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
    membershipRevisions: {},
  }));
  const [reloadVersion, setReloadVersion] = useState(0);
  const requestRef = useRef(null);
  const requestGeneration = useRef(0);
  const preserveSelectionOnReload = useRef(false);

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
      const preserveSelection = preserveSelectionOnReload.current;
      preserveSelectionOnReload.current = false;
      setState((previous) => {
        const saved = previous.selectedBandId ?? readSelectedId(userId);
        const selected = selectValidBand(saved, activeBands) || (!preserveSelection ? activeBands[0] : null) || null;
        const activeIds = new Set(activeBands.map((band) => Number(band.id)));
        const membershipRevisions = { ...previous.membershipRevisions };
        previous.activeBands.filter((band) => !activeIds.has(Number(band.id)) && band.slug).forEach((band) => {
          membershipRevisions[band.slug] = (membershipRevisions[band.slug] || 0) + 1;
        });
        writeSelectedId(userId, selected?.id ?? null);
        return { activeBands, pendingMemberships, bandsLoaded: true, loading: false, error: '', selectedBandId: selected?.id ?? null, membershipRevisions };
      });
    }).catch(() => {
      if (!current || generation !== requestGeneration.current) return;
      const preserveSelection = preserveSelectionOnReload.current;
      preserveSelectionOnReload.current = false;
        setState((previous) => ({ ...previous, bandsLoaded: true, loading: false, error: preserveSelection ? (previous.error || '밴드 정보를 새로 확인하지 못했습니다. 다시 시도해 주세요.') : '밴드 정보를 불러오지 못했습니다. 다시 시도해 주세요.' }));
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

  const applyBandLeave = useCallback((bandId, bandSlug) => {
    const id = Number(bandId);
    if (!Number.isInteger(id) || id <= 0) return;
    requestGeneration.current += 1;
    requestRef.current = null;
    preserveSelectionOnReload.current = false;
    setState((previous) => {
      const selectedBandId = Number(previous.selectedBandId) === id ? null : previous.selectedBandId;
      if (selectedBandId === null) writeSelectedId(userId, null);
      return {
        ...previous,
        activeBands: previous.activeBands.filter((band) => Number(band.id) !== id),
        selectedBandId,
        bandsLoaded: true,
        loading: false,
        error: '',
        membershipRevisions: { ...previous.membershipRevisions, [bandSlug]: (previous.membershipRevisions[bandSlug] || 0) + 1 },
      };
    });
    try { window.localStorage.setItem(membershipSignalKey(userId), JSON.stringify({ bandId: id, bandSlug, nonce: Date.now() })); } catch { /* tab-local state is already updated */ }
  }, [userId]);

  const refreshBandMembership = useCallback(async (bandId, bandSlug, signal) => {
    const id = Number(bandId);
    const response = await api.requestJson(`/api/v2/bands/by-slug/${encodeURIComponent(bandSlug)}`, signal ? { signal } : {});
    const band = response?.band;
    if (Number(band?.id) !== id) return null;
    const hasActiveMembership = Boolean(band.membership && (band.membership.status === undefined || band.membership.status === 'active'));
    setState((previous) => ({
      ...previous,
      activeBands: previous.activeBands.flatMap((item) => Number(item.id) !== id ? [item] : (hasActiveMembership ? [band] : [])),
      selectedBandId: hasActiveMembership ? previous.selectedBandId : (Number(previous.selectedBandId) === id ? null : previous.selectedBandId),
    }));
    if (!hasActiveMembership && Number(readSelectedId(userId)) === id) writeSelectedId(userId, null);
    return hasActiveMembership ? band : null;
  }, [api, userId]);

  const reload = useCallback(({ preserveSelection = false } = {}) => {
    if (!userId) return;
    // An explicit refresh must observe state after the triggering action,
    // rather than silently joining a read that began before it.
    requestGeneration.current += 1;
    requestRef.current = null;
    preserveSelectionOnReload.current = preserveSelection;
    setState((previous) => ({ ...previous, activeBands: preserveSelection ? previous.activeBands : [], pendingMemberships: preserveSelection ? previous.pendingMemberships : [], bandsLoaded: preserveSelection ? previous.bandsLoaded : false, loading: preserveSelection ? previous.loading : true, error: preserveSelection ? previous.error : '' }));
    setReloadVersion((version) => version + 1);
  }, [userId]);

  useEffect(() => {
    if (!userId) return undefined;
    const onStorage = (event) => {
      if (event.key !== membershipSignalKey(userId) || !event.newValue) return;
      let bandId; let bandSlug;
      try { const value = JSON.parse(event.newValue); bandId = Number(value?.bandId); bandSlug = value?.bandSlug; } catch { return; }
      if (!Number.isInteger(bandId) || bandId <= 0 || typeof bandSlug !== 'string' || !bandSlug) return;
      requestGeneration.current += 1;
      requestRef.current = null;
      preserveSelectionOnReload.current = true;
      setState((previous) => ({ ...previous, membershipRevisions: { ...previous.membershipRevisions, [bandSlug]: (previous.membershipRevisions[bandSlug] || 0) + 1 } }));
      setReloadVersion((version) => version + 1);
    };
    const onFocus = () => reload({ preserveSelection: true });
    window.addEventListener('storage', onStorage);
    window.addEventListener('focus', onFocus);
    return () => { window.removeEventListener('storage', onStorage); window.removeEventListener('focus', onFocus); };
  }, [reload, userId]);

  const selectedBand = selectValidBand(state.selectedBandId, state.activeBands);
  const value = useMemo(() => ({
    activeBands: state.activeBands,
    pendingMemberships: state.pendingMemberships,
    bandsLoaded: state.bandsLoaded,
    loading: state.loading,
    error: state.error,
    selectedBand,
    membershipRevisions: state.membershipRevisions,
    selectBand,
    clearSelectedBand,
    applyBandLeave,
    refreshBandMembership,
    reload,
  }), [applyBandLeave, clearSelectedBand, refreshBandMembership, reload, selectedBand, selectBand, state.activeBands, state.bandsLoaded, state.error, state.loading, state.membershipRevisions, state.pendingMemberships]);
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
