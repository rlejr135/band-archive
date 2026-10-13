import { useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../context/AuthContext.jsx';
import { createAuthenticatedApiClient } from '../../services/authApiClient.js';

// Platform privileges come only from the authenticated API response. Never
// infer them from the Supabase profile or email address.
export const usePlatformAdmin = () => {
  const { user, getAccessToken, refreshSession, loading: authLoading } = useAuth();
  const identity = user?.id || '';
  const api = useMemo(() => createAuthenticatedApiClient({ getAccessToken, refreshSession }), [getAccessToken, refreshSession]);
  const [result, setResult] = useState({ identity: '', loading: true, admin: false, error: null });

  useEffect(() => {
    let active = true;
    if (authLoading) return () => { active = false; };
    if (!identity) return () => { active = false; };
    api.requestJson('/api/v2/me').then((data) => {
      if (active) setResult({ identity, loading: false, admin: data?.user?.is_platform_admin === true, error: null });
    }).catch((error) => {
      if (active) setResult({ identity, loading: false, admin: false, error });
    });
    return () => { active = false; };
  }, [api, authLoading, identity]);

  if (authLoading) return { loading: true, admin: false, error: null };
  if (!identity) return { loading: false, admin: false, error: null };
  return result.identity === identity ? result : { loading: true, admin: false, error: null };
};
