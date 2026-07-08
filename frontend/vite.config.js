import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The React dev server proxies /api to the FastAPI backend on :8077,
// so the browser talks to a single origin (no CORS in dev) and SSE streams cleanly.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
