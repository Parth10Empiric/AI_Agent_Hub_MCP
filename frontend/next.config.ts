import type { NextConfig } from "next";

/**
 * The backend, as seen from the Next.js SERVER process.
 *
 * Note this is not NEXT_PUBLIC_*: the rewrite happens server-side, so
 * the browser never learns this address. In production the API may sit
 * on a private network the browser cannot reach at all.
 */
const API_ORIGIN =
  process.env.API_PROXY_ORIGIN ?? "http://127.0.0.1:8077";

const nextConfig: NextConfig = {
  /**
   * Where the build output goes.
   *
   * `next dev` and `next build` BOTH write here, and they write
   * incompatible things. Run a build while a dev server is up and the
   * dev server's chunks are overwritten - every page then renders blank
   * with "Cannot find module './611.js'" in the terminal.
   *
   * The end-to-end tests build a production bundle, so they would do
   * exactly that to whatever dev server is running. Pointing them at
   * their own directory makes the collision impossible instead of
   * something to remember.
   */
  distDir: process.env.NEXT_DIST_DIR ?? ".next",

  /**
   * Proxy /api/* to FastAPI so the whole app is ONE origin.
   *
   * WHY THIS EXISTS - IT IS NOT ABOUT CONVENIENCE
   *
   * The refresh token lives in an httpOnly cookie (Phase 4.2). Cookies
   * are governed by origin, and browsers treat localhost:3000 and
   * localhost:8077 as different origins. Talk to the API directly and
   * you inherit all of this:
   *
   *   - the cookie needs SameSite=None, which REQUIRES Secure, which
   *     means you need TLS in local development
   *   - CORS preflight on every mutating request
   *   - Access-Control-Allow-Credentials, and an allow-list that can
   *     never be "*" once credentials are involved
   *
   * Behind this rewrite the browser only ever sees /api/... on its own
   * origin. SameSite=Lax works, no preflight, no CORS config. The same
   * arrangement holds in production behind nginx or Vercel, so
   * development and production behave identically - which is the whole
   * point.
   */
  /**
   * Security headers on the HTML responses (Phase 5.9).
   *
   * These belong HERE and not on FastAPI: a Content-Security-Policy on
   * a JSON response protects nothing. It is the page that loads
   * scripts, and the page is served by Next.
   *
   * CSP IS REPORT-ONLY, DELIBERATELY.
   *
   * An enforcing policy that is slightly wrong breaks the app silently
   * in the browser and passes every server-side test - and the usual
   * response to that is to delete the header rather than fix it.
   * Report-Only sends the same violations to the console while
   * blocking nothing. Watch it for a week, then rename the header.
   *
   * `unsafe-inline` for styles is required by Next's own injected
   * styles; `unsafe-eval` is required by its dev mode only, which is
   * why the policy differs between environments.
   */
  async headers() {
    const dev = process.env.NODE_ENV !== "production";

    const csp = [
      "default-src 'self'",
      `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: https:",
      "font-src 'self' data:",
      // The API is same-origin thanks to the rewrite below, so this
      // does not need to name it.
      "connect-src 'self'",
      // Nothing in this app is ever framed, and nothing it shows
      // should be embeddable - least of all the approval dialog.
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; ");

    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy-Report-Only", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), payment=()",
          },
        ],
      },
    ];
  },

  async rewrites() {
    return {
      /**
       * `fallback`, NOT a plain array.
       *
       * Returning an array puts rewrites in the "afterFiles" bucket,
       * which Next applies BEFORE dynamic routes. The SSE proxy at
       * app/api/conversations/[id]/messages/stream/route.ts is a
       * dynamic route, so an array-form rewrite silently swallowed it -
       * the streaming handler never ran and every turn arrived in one
       * buffered burst at the end.
       *
       * `fallback` runs LAST, after every Next route has had its
       * chance. Real handlers win; everything else is forwarded.
       */
      fallback: [
        {
          source: "/api/:path*",
          destination: `${API_ORIGIN}/api/:path*`,
        },
      ],
    };
  },
};

export default nextConfig;
