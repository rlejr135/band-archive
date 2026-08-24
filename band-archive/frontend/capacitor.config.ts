import type { CapacitorConfig } from '@capacitor/cli';

// Override these defaults with CAPACITOR_APP_ID / CAPACITOR_APP_NAME in CI.
const config: CapacitorConfig = {
  appId: process.env.CAPACITOR_APP_ID || 'com.deutteun.archive',
  appName: process.env.CAPACITOR_APP_NAME || 'Deutteun Archive',
  webDir: 'dist',
};

export default config;
