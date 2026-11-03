import { resolve } from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import type { Plugin } from 'vite'

/** Strict CSP in production; development additionally allows Vite's HMR client. */
function csp(mode: string): Plugin {
  const prod = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: http://127.0.0.1:*",
    "connect-src 'self' http://127.0.0.1:*",
    "font-src 'self' data:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'"
  ]
  const dev = prod.map((d) =>
    d.startsWith('script-src')
      ? "script-src 'self' 'unsafe-inline'"
      : d.startsWith('connect-src')
        ? 'connect-src \'self\' http://127.0.0.1:* ws://localhost:* http://localhost:*'
        : d
  )
  const policy = (mode === 'production' ? prod : dev).join('; ')
  return {
    name: 'mmi-csp',
    transformIndexHtml: (html) =>
      html.replace('<!--CSP-->', `<meta http-equiv="Content-Security-Policy" content="${policy}" />`)
  }
}

export default defineConfig(({ mode }) => ({
  main: { plugins: [externalizeDepsPlugin()] },
  preload: { plugins: [externalizeDepsPlugin()] },
  renderer: {
    resolve: { alias: { '@': resolve('src/renderer/src') } },
    plugins: [react(), tailwindcss(), csp(mode)]
  }
}))
