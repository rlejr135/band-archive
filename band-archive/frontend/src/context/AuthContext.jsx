import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { getSupabaseClient, getSupabaseConfigError } from '../services/supabase.js';

const AuthContext = createContext(null);
const RECOVERY_MARKER = 'band-archive.password-recovery';

const setRecoveryMarker = (value) => {
  try {
    if (value) window.sessionStorage.setItem(RECOVERY_MARKER, '1');
    else window.sessionStorage.removeItem(RECOVERY_MARKER);
  } catch {
    // A blocked storage implementation must not prevent sign-in.
  }
};

const hasRecoveryMarker = () => {
  try {
    return window.sessionStorage.getItem(RECOVERY_MARKER) === '1';
  } catch {
    return false;
  }
};

export const AuthProvider = ({ children }) => {
  const configError = getSupabaseConfigError();
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(() => !configError);
  const [authEvent, setAuthEvent] = useState(null);

  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!supabase) return undefined;
    let active = true;
    const loadSession = async () => {
      const { data } = await supabase.auth.getSession();
      if (active) {
        setSession(data.session ?? null);
        setLoading(false);
      }
    };
    loadSession();
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, nextSession) => {
      if (!active) return;
      setAuthEvent(event);
      setSession(nextSession ?? null);
      if (event === 'PASSWORD_RECOVERY' && nextSession) setRecoveryMarker(true);
      if (event === 'SIGNED_OUT') setRecoveryMarker(false);
      setLoading(false);
    });
    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, []);

  const getAccessToken = useCallback(async () => {
    const supabase = getSupabaseClient();
    if (!supabase) return null;
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  }, []);

  const refreshSession = useCallback(async () => {
    const supabase = getSupabaseClient();
    if (!supabase) return null;
    const { data } = await supabase.auth.refreshSession();
    return data.session?.access_token ?? null;
  }, []);

  const signOut = useCallback(async () => {
    const supabase = getSupabaseClient();
    if (supabase) await supabase.auth.signOut();
    setRecoveryMarker(false);
  }, []);

  const value = useMemo(() => ({
    session,
    user: session?.user ?? null,
    loading,
    configError,
    authEvent,
    hasRecoverySession: Boolean(session && hasRecoveryMarker()),
    getAccessToken,
    refreshSession,
    signOut,
    clearRecoverySession: () => setRecoveryMarker(false),
  }), [authEvent, configError, getAccessToken, loading, refreshSession, session, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider');
  return context;
};
