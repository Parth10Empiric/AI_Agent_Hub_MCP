/**
 * The one place the frontend talks to the backend.
 *
 * Every request in the app goes through `api()`. That is deliberate:
 * authentication, token refresh, error shaping and JSON parsing are
 * decisions you want to make ONCE. Scatter raw fetch() calls through
 * components and each one is a chance to forget the Authorization
 * header or to swallow an error.
 */

import {
  getAccessToken,
  notifySessionEnded,
  setAccessToken,
} from "./tokens";

// Same-origin by default in the browser thanks to the rewrite in
// next.config.ts - see the comment there for why that matters to
// cookies.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/**
 * The backend's error shape, from Phase 3.10.
 *
 * One vocabulary from executor to HTTP means the UI learns one set of
 * codes - `tool_permission_denied` means the same thing here as it
 * does in agent/errors.py.
 */
export interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
    request_id?: string;
  };
  // FastAPI's own validation errors come back as `detail`, which is
  // either a string or a list of per-field objects.
  detail?: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly requestId: string | undefined;
  readonly details: Record<string, unknown> | undefined;

  /**
   * Seconds to wait, from the Retry-After header on a 429.
   *
   * Carried on the error rather than left in the response, because by
   * the time a component catches this the response is long gone - and
   * "try again later" with no number is what turns a polite client
   * into a hot loop.
   */
  readonly retryAfter: number | undefined;

  constructor(
    status: number,
    body: ApiErrorBody | null,
    fallback: string,
    retryAfter?: number,
  ) {
    super(pickMessage(body, fallback));

    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code;
    this.requestId = body?.error?.request_id;
    this.details = body?.error?.details;
    this.retryAfter = retryAfter;
  }

  /** True when the server refused because of a rate limit. */
  get isRateLimited(): boolean {
    return this.status === 429;
  }
}

function pickMessage(body: ApiErrorBody | null, fallback: string): string {
  if (body?.error?.message) return body.error.message;

  if (typeof body?.detail === "string") return body.detail;

  // 422 from FastAPI: detail is an array of {loc, msg, type}. Showing
  // the first message is far more useful than "Request failed".
  if (Array.isArray(body?.detail)) {
    const first = body.detail[0] as { msg?: string } | undefined;
    if (first?.msg) return first.msg;
  }

  return fallback;
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  /** Sent as JSON. Use `rawBody` if you need FormData or a stream. */
  json?: unknown;
  rawBody?: BodyInit | null;
  /** Internal: prevents a refresh loop. Never set this yourself. */
  _isRetry?: boolean;
}

/**
 * A single in-flight refresh, shared by every caller.
 *
 * THE PROBLEM THIS SOLVES
 *
 * A dashboard fires six queries at once. The access token has just
 * expired, so all six get 401 at the same moment. Without this, all
 * six call /api/auth/refresh simultaneously.
 *
 * The backend ROTATES refresh tokens on every use (Phase 3.4), so the
 * first call invalidates the token the other five are still holding.
 * Five of them fail, and the user is bounced to /login despite having
 * a perfectly valid session.
 *
 * Storing the PROMISE means the first 401 starts the refresh and the
 * other five await that same promise. One network call, one rotation,
 * six retried requests.
 */
let refreshInFlight: Promise<string | null> | null = null;

export async function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const response = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },

        // The refresh token is an httpOnly cookie. This is the line
        // that tells the browser to attach it - fetch does NOT send
        // cookies by default on cross-origin requests, and omitting
        // this produces a 401 that looks exactly like an expired
        // session.
        credentials: "include",

        // The endpoint accepts the token from the cookie OR the body.
        // The body must still be valid JSON, hence {}.
        body: JSON.stringify({}),
      });

      if (!response.ok) return null;

      const data = (await response.json()) as { access_token?: string };
      const token = data.access_token ?? null;

      setAccessToken(token);
      return token;
    } catch {
      // A network failure is not a session failure, but we cannot tell
      // the difference here. Returning null lets the caller decide.
      return null;
    } finally {
      // Cleared no matter what, or a single failed refresh would be
      // cached forever and the app could never recover.
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

/**
 * Perform an authenticated request.
 *
 * On 401 it refreshes ONCE and retries ONCE. Never more - an
 * unconditional retry loop against an endpoint that keeps returning
 * 401 is a denial-of-service attack on your own backend.
 */
export async function api<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { json, rawBody, _isRetry, headers, ...rest } = options;

  const requestHeaders = new Headers(headers);

  if (json !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
  }

  const token = getAccessToken();
  if (token) {
    requestHeaders.set("Authorization", `Bearer ${token}`);
  }

  // Was there a session to lose? Captured BEFORE the request, because
  // a failed refresh clears the token and we would otherwise never be
  // able to tell the two cases apart. See the 401 branch below.
  const hadSession = token !== null;

  const response = await fetch(`${API_BASE}${path}`, {
    ...rest,
    headers: requestHeaders,
    credentials: "include",
    body: json !== undefined ? JSON.stringify(json) : rawBody,
  });

  if (response.status === 401 && !_isRetry) {
    const fresh = await refreshAccessToken();

    if (fresh) {
      return api<T>(path, { ...options, _isRetry: true });
    }

    // Refresh failed. Whether that MATTERS depends on whether the user
    // was signed in to begin with.
    //
    //   hadSession true   a real session expired and could not be
    //                     renewed. Announce it; the app redirects to
    //                     /login.
    //
    //   hadSession false  nobody was signed in. This is the ordinary
    //                     first-load probe: restoreSession() asks the
    //                     server whether a refresh cookie exists, and
    //                     "no" is the expected answer for a visitor.
    //
    // Announcing in the second case is a real bug, not a nicety: it
    // fires router.replace("/login") on page load, so a logged-out
    // person cannot stay on /register long enough to sign up.
    if (hadSession) {
      notifySessionEnded();
    }
  }

  if (!response.ok) {
    throw new ApiError(
      response.status,
      await readErrorBody(response),
      `Request failed with status ${response.status}`,
      parseRetryAfter(response),
    );
  }

  // 204 No Content has an empty body. Calling .json() on it throws a
  // SyntaxError that looks like a server bug but is entirely ours.
  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function readErrorBody(
  response: Response,
): Promise<ApiErrorBody | null> {
  try {
    return (await response.json()) as ApiErrorBody;
  } catch {
    // An HTML error page from a proxy, or an empty body. Either way
    // there is nothing structured to read, and throwing here would
    // replace a useful HTTP status with a JSON parse error.
    return null;
  }
}


/**
 * Retry-After, in seconds.
 *
 * The header is allowed to be either a number of seconds or an HTTP
 * date. This API always sends seconds, but a proxy in front of it may
 * not - and a NaN silently becoming "0 seconds" would tell the user to
 * retry immediately, straight back into the limit.
 */
function parseRetryAfter(response: Response): number | undefined {
  const raw = response.headers.get("retry-after");

  if (!raw) return undefined;

  const seconds = Number(raw);

  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds);

  const asDate = Date.parse(raw);

  if (Number.isFinite(asDate)) {
    return Math.max(0, Math.ceil((asDate - Date.now()) / 1000));
  }

  return undefined;
}
