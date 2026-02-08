import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // GitHub Pages에서는 repository 이름이 base path가 됩니다
  // 예: https://username.github.io/band-archive/
  base: process.env.GITHUB_PAGES ? '/band-archive/' : '/',
})
