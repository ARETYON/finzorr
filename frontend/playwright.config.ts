// finzorr E2E — runs against the LOCAL dev stack (backend :8000 with
// APP_ENV=dev + DEV_FAKE_AUTH=true and the local LLM, frontend :5173).
// Deliberately NOT in PR CI: the chat flow needs a live model.
//   cd frontend && npx playwright test
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev',
    cwd: '.',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 30_000,
  },
})
