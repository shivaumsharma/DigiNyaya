import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The React dev server proxies /api to the FastAPI backend on :8077,
// so the browser talks to a single origin (no CORS in dev) and SSE streams cleanly.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    globals: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // New auth endpoints live bare at /auth/* and /me (not under /api),
      // per the spec's endpoint list -- proxied separately so the browser
      // still talks to a single origin in dev.
      '/auth': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/me': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
