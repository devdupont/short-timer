import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    // Hooks and components need a DOM. The pure-logic suites don't care, and
    // run fine under it, so one environment beats annotating files.
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
  },
})
