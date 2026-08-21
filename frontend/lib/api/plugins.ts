/**
 * Plugin catalogue and connections.
 *
 * Everything the catalogue returns is derived from the LIVE tool
 * registry on the backend, not a hardcoded list. Add a service to the
 * MCP server and it appears here with its tools, risk levels and
 * approval flags already filled in - no frontend change at all.
 */

import { api } from "./client";
import type {
  ConnectRequest,
  ConnectionRead,
  OAuthStart,
  PluginDetail,
  PluginSummary,
} from "../types";

export function listPlugins(): Promise<PluginSummary[]> {
  return api<PluginSummary[]>("/api/plugins");
}

export function getPlugin(key: string): Promise<PluginDetail> {
  return api<PluginDetail>(`/api/plugins/${encodeURIComponent(key)}`);
}

export function listConnections(): Promise<ConnectionRead[]> {
  return api<ConnectionRead[]>("/api/plugins/connections");
}

export function connectPlugin(
  key: string,
  payload: ConnectRequest,
): Promise<ConnectionRead> {
  return api<ConnectionRead>(
    `/api/plugins/${encodeURIComponent(key)}/connect`,
    { method: "POST", json: payload },
  );
}

export function disconnectPlugin(key: string): Promise<void> {
  return api<void>(`/api/plugins/${encodeURIComponent(key)}/connect`, {
    method: "DELETE",
  });
}

/**
 * Begin an OAuth flow.
 *
 * Returns a URL rather than redirecting, and the caller navigates to
 * it. That inversion exists because this endpoint is bearer
 * authenticated and a browser NAVIGATION cannot send an Authorization
 * header - so the app fetches the URL with a normal authenticated
 * request first, then leaves.
 *
 * Use `window.location.href = authorize_url`, never fetch(): a consent
 * screen is a page the user has to see and interact with, and no
 * amount of XHR will make a provider render one inside your app.
 */
export function startOAuth(
  key: string,
  redirectTo?: string,
): Promise<OAuthStart> {
  const query = redirectTo
    ? `?redirect_to=${encodeURIComponent(redirectTo)}`
    : "";

  return api<OAuthStart>(
    `/api/plugins/${encodeURIComponent(key)}/oauth/start${query}`,
    { method: "POST" },
  );
}

/** Force a refresh. Normal operation refreshes before use, not here. */
export function refreshConnection(key: string): Promise<ConnectionRead> {
  return api<ConnectionRead>(
    `/api/plugins/${encodeURIComponent(key)}/oauth/refresh`,
    { method: "POST" },
  );
}
