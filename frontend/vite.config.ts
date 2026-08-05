import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev proxy: /api and /ws go to the local FastAPI backend so the SPA can use
// relative URLs in dev (no VITE_API_BASE_URL needed until uat/prod).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true },
      '/healthz': { target: 'http://localhost:8000' },
    },
  },
})
