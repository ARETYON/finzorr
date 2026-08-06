// Separate from vite.config.ts on purpose: the app builds with rolldown-vite,
// vitest bundles rollup-vite, and their plugin types are incompatible in one
// file. Tests don't need the react plugin — esbuild's automatic JSX runtime
// transforms TSX on its own.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    globals: false,
    // e2e/ is Playwright's — its test() throws if vitest collects it
    exclude: ['e2e/**', 'node_modules/**'],
  },
})
