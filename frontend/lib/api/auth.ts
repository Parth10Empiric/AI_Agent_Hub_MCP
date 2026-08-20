/**
 * Auth calls.
 *
 * Each function is a thin, typed wrapper over `api()`. The value is not
 * the code saved - it is that the URL string and the response type are
 * written down in exactly one place. A component never types a path.
 */

import { api } from "./client";
import { clearAccessToken, setAccessToken } from "./tokens";
import type {
  AuthResponse,
  LoginRequest,
  RegisterRequest,
  User,
} from "../types";

export async function register(
  payload: RegisterRequest,
): Promise<AuthResponse> {
  const result = await api<AuthResponse>("/api/auth/register", {
    method: "POST",
    json: payload,
  });

  // Registering logs you in. The token goes straight into memory so the
  // very next request is authenticated without a round trip to /login.
  setAccessToken(result.access_token);
  return result;
}

export async function login(payload: LoginRequest): Promise<AuthResponse> {
  const result = await api<AuthResponse>("/api/auth/login", {
    method: "POST",
    json: payload,
  });

  setAccessToken(result.access_token);
  return result;
}

export async function logout(): Promise<void> {
  try {
    // Tells the SERVER to revoke the refresh token. Without this the
    // cookie is gone from this browser but the token remains valid -
    // anyone who captured it could still use it for thirty days.
    //
    // `json: {}` is not optional. The endpoint declares a RefreshRequest
    // body (it accepts the token from the body for CLI clients that
    // hold no cookies), so a POST with NO body is rejected as 422
    // before any of the logout logic runs.
    await api<void>("/api/auth/logout", { method: "POST", json: {} });
  } catch {
    // SWALLOWED ON PURPOSE.
    //
    // "Sign out" must always sign the user out of this browser. If the
    // server is unreachable, or the token was already revoked, the
    // correct outcome is still a signed-out browser - not an error
    // message on a page the user believes they have left.
    //
    // The local token is cleared below either way, so the worst case
    // is a refresh cookie that outlives its usefulness on the server.
  } finally {
    clearAccessToken();
  }
}

export function me(): Promise<User> {
  return api<User>("/api/auth/me");
}

/**
 * Restore a session on page load.
 *
 * The access token died with the last page. The refresh cookie did not,
 * so this asks the backend to mint a new one. A failure here is the
 * normal case for a logged-out visitor, not an error worth showing.
 */
export async function restoreSession(): Promise<User | null> {
  try {
    const tokens = await api<{ access_token: string }>(
      "/api/auth/refresh",
      { method: "POST", json: {} },
    );

    setAccessToken(tokens.access_token);
    return await me();
  } catch {
    clearAccessToken();
    return null;
  }
}
