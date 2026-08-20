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
