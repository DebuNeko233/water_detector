import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import electron from 'vite-plugin-electron/simple'
import path from 'node:path'

export default defineConfig({
  base: './',
  plugins: [vue(), ...(process.env.WAFER_UI_ONLY === '1' ? [] : [electron({
    main: { entry: 'electron/main.ts' },
    preload: {
      input: path.resolve(__dirname, 'electron/preload.ts'),
      vite: { build: { rollupOptions: { output: {
        format: 'cjs', inlineDynamicImports: true,
        entryFileNames: 'preload.cjs', chunkFileNames: '[name].cjs',
      } } } },
    },
  })])],
  server: {host: '127.0.0.1', port: 5173, strictPort: true},
})
