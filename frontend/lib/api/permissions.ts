/**
 * Agent scopes and the permission audit trail (Phase 5.1).
 *
 * A SCOPE is a coarse grant - "this agent may read GitHub" - written
 * as service:resource:action. It is the ONLY permission the UI grants.
 *
 * There was once a second, finer gate: a per-tool checkbox grid. It
 * answered a near-identical question with a worse instrument - 108
 * boxes nobody reads, where a ticked write tool with no scope behind it
 * silently could not run. The grid is gone; a scope is one sentence a
 * person can actually mean, and it keeps meaning the same thing as the
 * tool catalogue grows.
 */

import { api } from "./client";
import type {
  AgentScopes,
  PermissionAuditPage,
} from "../types";

export function getAgentScopes(agentId: string): Promise<AgentScopes> {
  return api<AgentScopes>(`/api/agents/${agentId}/scopes`);
}

export function grantScope(
  agentId: string,
  scope: string,
): Promise<AgentScopes> {
  return api<AgentScopes>(`/api/agents/${agentId}/scopes`, {
    method: "POST",
    json: { scope },
  });
}

/**
 * Revoke one scope.
 *
 * The scope travels as a QUERY parameter, not a path segment. Scope
 * strings contain ":" and "*", and proxies, routers and HTTP clients
 * disagree about escaping those inside a path - a rule that only
 * sometimes reaches the server is not a security control.
 */
export function revokeScope(
  agentId: string,
  scope: string,
): Promise<AgentScopes> {
  return api<AgentScopes>(
    `/api/agents/${agentId}/scopes?scope=${encodeURIComponent(scope)}`,
    { method: "DELETE" },
  );
}

export function getAgentAudit(
  agentId: string,
  options: { limit?: number; cursor?: string } = {},
): Promise<PermissionAuditPage> {
  const params = new URLSearchParams();

  if (options.limit) params.set("limit", String(options.limit));
  if (options.cursor) params.set("cursor", options.cursor);

  const query = params.toString();

  return api<PermissionAuditPage>(
    `/api/agents/${agentId}/audit${query ? `?${query}` : ""}`,
  );
}
