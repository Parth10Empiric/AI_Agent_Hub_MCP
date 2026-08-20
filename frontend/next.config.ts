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
