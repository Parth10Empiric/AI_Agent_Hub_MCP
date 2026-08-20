/**
 * The access token store.
 *
 * A module-level variable. Not localStorage, not sessionStorage, not a
 * cookie this code can read.
 *
 * WHY A PLAIN VARIABLE IS THE SECURE CHOICE
 *
 * localStorage is readable by ANY script running on the page. One
 * malicious dependency, one injected <script>, and the attacker walks
 * away with a bearer token they can replay from their own machine for
 * as long as it lives.
 *
 * A module-level variable lives in the JavaScript heap. There is no
 * `localStorage.getItem`-style API to enumerate it, and it dies with
 * the tab.
 *
 * "But it is lost on refresh!" - yes, and that is the FEATURE. The
 * refresh token sits in an httpOnly cookie that JavaScript cannot read
 * at all; on reload the app asks /api/auth/refresh and the browser
 * attaches that cookie automatically. The session survives; the
 * credential never passes through readable storage.
 */

let accessToken: string | null = null;

/**
 * Callbacks fired when the session ends for good.
 *
 * The client cannot navigate on its own - it is plain TypeScript with
 * no router. So it announces "the session is gone" and whatever owns
 * the routing decides what that means (redirect to /login).
 *
 * This keeps lib/api free of React and Next.js imports, which is what
 * lets the same client run in a test, a script, or a server component.
 */
const sessionEndedHandlers = new Set<() => void>();

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function clearAccessToken(): void {
  accessToken = null;
}

export function onSessionEnded(handler: () => void): () => void {
  sessionEndedHandlers.add(handler);

  // Returns an UNSUBSCRIBE function, the same shape React's useEffect
  // expects as a cleanup. Without it, every component mount would add
  // another handler and none would ever be removed.
  return () => sessionEndedHandlers.delete(handler);
}

export function notifySessionEnded(): void {
  accessToken = null;
  sessionEndedHandlers.forEach((handler) => handler());
}
