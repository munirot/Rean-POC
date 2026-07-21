import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy /api to the Python face-service so the browser stays same-origin
// (also keeps the live camera in a secure context on http://localhost:5173).
export default defineConfig({
  plugins: [react()],
  css: {
    preprocessorOptions: {
      scss: {
        // Use the modern Sass compiler API and hide Bootstrap 5.3's @import /
        // legacy-function deprecation warnings (they're harmless noise).
        api: 'modern-compiler',
        quietDeps: true,
        silenceDeprecations: [
          'import', 'legacy-js-api', 'global-builtin', 'color-functions', 'if-function',
        ],
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
