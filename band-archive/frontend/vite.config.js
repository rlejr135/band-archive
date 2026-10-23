import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  ...(globalThis.process?.env?.ANYTHING_ISOLATED_TEST === '1' ? { envDir: false } : {}),
  plugins: [react()],
  base: '/',
})
