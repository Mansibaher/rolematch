import { defineConfig, devices } from "@playwright/test";

// Expects the API (:8000, seeded with `python -m app.cli seed`) and the frontend (:3000) to be running,
// or set PLAYWRIGHT_START_SERVERS=1 to have Playwright start them (used in CI).
const start = process.env.PLAYWRIGHT_START_SERVERS === "1";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ["--no-sandbox"] } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: start
    ? [
        { command: "cd ../backend && uvicorn app.main:app --port 8000", url: "http://localhost:8000/api/health", reuseExistingServer: true, timeout: 120_000 },
        { command: "npx next start -p 3000", url: "http://localhost:3000/login", reuseExistingServer: true, timeout: 120_000 },
      ]
    : undefined,
});
