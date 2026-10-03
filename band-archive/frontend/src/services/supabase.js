import { createClient } from '@supabase/supabase-js';
import { Capacitor } from '@capacitor/core';
import { getBackgroundUploadPlugin } from './uploadTransport.js';

const config = {
  url: import.meta.env.VITE_SUPABASE_URL,
  anonKey: import.meta.env.VITE_SUPABASE_ANON_KEY,
};

let client;
const BackgroundUpload = getBackgroundUploadPlugin();
const isNativeSecureSession = () => Capacitor.isNativePlatform() && ['android', 'ios'].includes(Capacitor.getPlatform());
const isSessionKey = (key) => String(key || '').endsWith('-auth-token');

// Supabase permits promise-based storage. On native platforms, its refresh token
// stays behind the plugin's Keychain/Keystore-backed storage; PKCE verifier state is
// short-lived sessionStorage and never contains the persisted auth session.
const nativeAuthStorage = {
  getItem: async (key) => {
    if (!isSessionKey(key)) return window.sessionStorage.getItem(key);
    const value = await BackgroundUpload.getSecureAuthSession();
    return value?.value ?? null;
  },
  setItem: async (key, value) => {
    if (!isSessionKey(key)) { window.sessionStorage.setItem(key, value); return; }
    await BackgroundUpload.setSecureAuthSession({ value, supabaseUrl: config.url, anonKey: config.anonKey });
  },
  removeItem: async (key) => {
    if (!isSessionKey(key)) { window.sessionStorage.removeItem(key); return; }
    await BackgroundUpload.removeSecureAuthSession();
  },
};

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
        storage: isNativeSecureSession() ? nativeAuthStorage : window.localStorage,
      },
    });
  }
  return client;
};
