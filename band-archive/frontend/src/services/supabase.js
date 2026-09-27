import { createClient } from '@supabase/supabase-js';

const config = {
  url: import.meta.env.VITE_SUPABASE_URL,
  anonKey: import.meta.env.VITE_SUPABASE_ANON_KEY,
};

let client;

export const getSupabaseConfigError = () => {
  if (!config.url || !config.anonKey) {
    return 'Supabase 환경 설정이 없습니다. VITE_SUPABASE_URL 및 VITE_SUPABASE_ANON_KEY를 설정하세요.';
  }
  try {
    new URL(config.url);
    return null;
  } catch {
    return 'Supabase URL 환경 설정이 올바르지 않습니다.';
  }
};

export const getSupabaseClient = () => {
  const configError = getSupabaseConfigError();
  if (configError) return null;
  if (!client) {
    client = createClient(config.url, config.anonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
        storage: window.localStorage,
      },
    });
  }
  return client;
};
