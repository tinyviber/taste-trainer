import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // Keep the app token on the local dev proxy rather than exposing it to the browser.
  const env = loadEnv(mode, '..', '')
  const processEnv = (globalThis as typeof globalThis & {
    process?: { env?: Record<string, string | undefined> }
  }).process?.env ?? {}
  const apiToken = env.API_TOKEN ?? processEnv.API_TOKEN
  const authHeaders = apiToken ? { 'X-Token': apiToken } : undefined

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': { target: 'http://127.0.0.1:8421', headers: authHeaders },
        '/provider-api': {
          target: 'http://127.0.0.1:8765',
          headers: authHeaders,
          rewrite: (path) => path.replace(/^\/provider-api/, ''),
        },
      },
    },
  }
})
