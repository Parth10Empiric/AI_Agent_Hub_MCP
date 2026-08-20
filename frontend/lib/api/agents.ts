/**
 * Agent CRUD and tool selection.
 */

import { api } from "./client";
import type {
  AgentCreate,
  AgentDetail,
  AgentSummary,
  AgentUpdate,
} from "../types";

export function listAgents(): Promise<AgentSummary[]> {
  return api<AgentSummary[]>("/api/agents");
}

export function getAgent(id: string): Promise<AgentDetail> {
  return api<AgentDetail>(`/api/agents/${id}`);
}

/**
 * Fields the SERVER fills in when they are omitted.
 *
 * openapi-typescript marks any field carrying a `default` as required,
 * because a RESPONSE always contains it. For a request body that is the
 * wrong way round - the whole point of a default is that the client may
 * leave it out. Making them optional here keeps the model name and
 * temperature owned by the backend instead of hardcoded in the UI.
 */
export type AgentCreatePayload = Omit<
  AgentCreate,
  "model" | "temperature"
> &
  Partial<Pick<AgentCreate, "model" | "temperature">>;

export function createAgent(
  payload: AgentCreatePayload,
): Promise<AgentDetail> {
  return api<AgentDetail>("/api/agents", { method: "POST", json: payload });
}

export function updateAgent(
  id: string,
  payload: AgentUpdate,
): Promise<AgentDetail> {
  return api<AgentDetail>(`/api/agents/${id}`, {
    method: "PATCH",
    json: payload,
  });
}

/** Soft delete. The backend archives; conversations keep their agent. */
export function archiveAgent(id: string): Promise<void> {
  return api<void>(`/api/agents/${id}`, { method: "DELETE" });
}

export function getAgentTools(id: string): Promise<AgentDetail> {
  return api<AgentDetail>(`/api/agents/${id}/tools`);
}

/**
 * Replace this agent's tool selection.
 *
 * PUT, not PATCH: the request body IS the complete new selection. That
 * matters for a checkbox grid - a partial update has no way to express
 * "this box was UNCHECKED", because an absent key is indistinguishable
 * from a key the client did not know about.
 *
 * `tools` is a MAP keyed by tool name, matching AgentToolsUpdate on the
 * backend - not a list. The map shape makes the key unique by
 * construction, so the request cannot contain the same tool twice with
 * two different answers.
 */
export type ToolSelection = Record<
  string,
  { enabled?: boolean; requires_approval?: boolean | null }
>;

export function setAgentTools(
  id: string,
  tools: ToolSelection,
): Promise<AgentDetail> {
  return api<AgentDetail>(`/api/agents/${id}/tools`, {
    method: "PUT",
    json: { tools },
  });
}
