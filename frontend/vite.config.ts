import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteStaticCopy } from 'vite-plugin-static-copy'

export default defineConfig({
  plugins: [
    react(),
    viteStaticCopy({
      targets: [
        {
          src: [
            'node_modules/@ruffle-rs/ruffle/ruffle.js',
            'node_modules/@ruffle-rs/ruffle/core.ruffle.*.js',
            'node_modules/@ruffle-rs/ruffle/*.wasm',
          ],
          dest: 'ruffle',
          rename: { stripBase: true },
        },
      ],
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
