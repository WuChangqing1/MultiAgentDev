import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Plain JS (not TS) on purpose: Vite can then load this config directly without
 * bundling it through esbuild, which keeps startup fast and avoids spawning a
 * helper process just to read configuration.
 *
 * The browser talks ONLY to this backend. It must never reach llama.cpp
 * directly -- all local-model traffic is proxied through FastAPI.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        // SSE must not be buffered by the dev proxy.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (String(proxyRes.headers['content-type'] ?? '').includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache, no-transform'
            }
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
})
