import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'fs'
import { join } from 'path'
import { homedir } from 'os'

// Single source of truth for the local control token.
//
// The backend reads its per-launch token from SYTHION_HANDSHAKE_TOKEN_FILE, or
// from <data dir>/token.json (default %LOCALAPPDATA%\Sythion (Synth)). In the
// packaged app the Tauri shell hands the token to the frontend at runtime. In
// browser dev there is no shell, so the dev proxy reads the SAME file on every
// request and injects X-Control-Token. This makes the token file authoritative:
// restarting the backend can never drift from the browser, and no secret is
// baked into the client bundle at build time.
const localAppData = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local')
const tokenFile =
  process.env.SYTHION_HANDSHAKE_TOKEN_FILE ||
  join(localAppData, 'Sythion (Synth)', 'token.json')

function liveControlToken(): string {
  try {
    // Strip a possible UTF-8 BOM (PowerShell writes one by default).
    const raw = readFileSync(tokenFile, 'utf8').replace(/^\uFEFF/, '')
    const token = JSON.parse(raw)?.token
    return typeof token === 'string' && token ? token : ''
  } catch {
    return ''
  }
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            const token = liveControlToken()
            // The proxy is authoritative in dev: overwrite any stale token the
            // browser may have sent from VITE_SYTHION_CONTROL_TOKEN.
            if (token) proxyReq.setHeader('X-Control-Token', token)
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
