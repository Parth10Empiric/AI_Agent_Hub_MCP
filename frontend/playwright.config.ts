import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the REAL stack.
 *
 * Phase 4.10 suggests mocking the backend so a failing test means the
 * UI broke rather than the network. That is right for a large suite -
 * but these tests exist to prove the whole product works together, and
 * a mock cannot prove that. The split used here:
 *
 *   auth / plugins / agents   real backend, fully deterministic
 *   chat                      real backend AND a real model, so it is
 *                             slow and lives behind its own timeout
 *
 * Runs against a PRODUCTION build, not `next dev`.
 *
 * That is not a detail. In dev, Next compiles each route the first time
 * it is requested, so the first visit to a page can take 15-20 seconds
 * while later visits take 200ms. Tests then fail or pass depending on
 * which one happened to warm the route - the failures move around
 * between runs and look like flaky application code when the
 * application is fine.
 *
 * The production server serves pre-built pages, so timings are stable
 * and a failure means something is actually broken. It also tests the
 * bundle users will really run.
 *
 * The API must be running separately:
 *   uvicorn api.main:app --port 8077
 */
export default defineConfig({
  testDir: "./tests/e2e",

  // Serial, not parallel. Every test registers a real user against one
  // shared PostgreSQL database; running them concurrently would make
  // failures depend on timing rather than on the code.
  fullyParallel: false,
  workers: 1,

  // A failing assertion should never be retried into a pass. A flaky
  // test that "passes on retry" is a bug report you threw away.
  retries: 0,

  reporter: [["list"]],

  timeout: 60_000,

  expect: {
    // Generous, because a tool-using turn genuinely takes seconds.
    timeout: 15_000,
  },

  // Playwright builds and starts the app itself, then shuts it down.
  // reuseExistingServer keeps local runs fast when a server is already
  // up, but CI always gets a clean one.
  webServer: {
    // NEXT_DIST_DIR keeps this build out of `.next`, so running the
    // test suite can never break a dev server someone has open.
    command:
      "NEXT_DIST_DIR=.next-e2e npm run build && " +
      "NEXT_DIST_DIR=.next-e2e npm run start -- --port 3100",
    url: "http://127.0.0.1:3100/login",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    stdout: "ignore",
    stderr: "pipe",
  },

  use: {
    baseURL: "http://127.0.0.1:3100",

    // Captured only for failures - a trace per passing test is a lot of
    // disk for something nobody will open.
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
