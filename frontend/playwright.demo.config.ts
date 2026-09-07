import { defineConfig, devices } from "@playwright/test";

/**
 * The documentation capture run. NOT a test suite.
 *
 * `playwright.config.ts` next door proves the product works against
 * the real stack. This one renders the screenshots in docs/media, and
 * it is deliberately a separate config rather than a flag on that one,
 * because the two want opposite things:
 *
 *   e2e         real backend, real model, assertions
 *   demo        scripted backend, fixed data, no assertions
 *
 * Sharing a config would mean every documentation re-render needed
 * working GitHub credentials and a running model.
 *
 *   npm run capture
 */
export default defineConfig({
  testDir: "./tests/demo",

  // One scripted walkthrough. Order matters and state carries between
  // steps, so there is nothing to parallelise.
  fullyParallel: false,
  workers: 1,
  retries: 0,

  reporter: [["list"]],

  // Generous: two whole agent turns run inside the single walkthrough,
  // and each one deliberately takes seconds.
  timeout: 300_000,
  expect: { timeout: 30_000 },

  // Playwright insists on somewhere to put per-test artefacts. The
  // screenshots do not go here - capture.spec.ts writes those straight
  // to docs/media - so this only ever holds failure traces.
  outputDir: "./tests/demo/.output",

  webServer: [
    {
      // The stand-in backend. See mock-api.mjs for why the images are
      // not captured against the real one.
      command: "node tests/demo/mock-api.mjs --port 8099",
      url: "http://127.0.0.1:8099/api/auth/me",
      reuseExistingServer: false,
      timeout: 30_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      // A PRODUCTION build, for the same reason the e2e config uses
      // one: `next dev` compiles each route on first request, so the
      // first visit to a page can sit on a blank screen for fifteen
      // seconds - long enough for a screenshot to catch it.
      //
      // NEXT_DIST_DIR keeps this build out of `.next`, so capturing
      // the docs cannot break a dev server someone has open.
      command:
        "NEXT_DIST_DIR=.next-demo API_PROXY_ORIGIN=http://127.0.0.1:8099 npm run build && " +
        "NEXT_DIST_DIR=.next-demo API_PROXY_ORIGIN=http://127.0.0.1:8099 npm run start -- --port 3200",
      url: "http://127.0.0.1:3200/login",
      reuseExistingServer: false,
      timeout: 240_000,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],

  use: {
    baseURL: "http://127.0.0.1:3200",

    // 1440x900 is a laptop, not a 4K desktop. A screenshot taken at a
    // width nobody uses shows a layout nobody sees.
    viewport: { width: 1440, height: 900 },

    // Retina. A 1x screenshot of a text-heavy UI looks blurry the
    // moment GitHub scales it into a README column.
    deviceScaleFactor: 2,

    // Every image this run produces is an explicit, waited-for
    // page.screenshot() in the spec. Playwright's own automatic
    // capture would only add failure artefacts nobody publishes.
    screenshot: "off",
    video: "off",

    // Pinned, not inherited. The app follows the system theme, so
    // without this the images would come out light or dark depending
    // on the desktop settings of whoever ran the capture.
    colorScheme: "light",
  },

  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
