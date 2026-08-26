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

/**
 * The agent, WITH its full tool list.
 *
 * Read-only from the UI's point of view now. Nothing on screen edits a
 * tool row any more - a user grants a scope instead, and each tool's
 * `permitted` flag says whether their scopes cover it. The PUT endpoint
 * behind `/tools` still exists for API clients that want the narrow
 * "hide this one tool" lever; the web app deliberately does not offer
 * it, because a second gate the user can shut invisibly is what the
 * checkbox grid got wrong.
 */
export function getAgentTools(id: string): Promise<AgentDetail> {
  return api<AgentDetail>(`/api/agents/${id}/tools`);
}


/**
 * Set which SERVICES this agent draws tools from.
 *
 * THE LEVER THAT WAS MISSING.
 *
 * An agent only ever sees tools from a service it was given, and until
 * this existed the only place to give it one was the create wizard.
 * Connect Google Drive afterwards and every existing agent stayed
 * blind to it - while the permissions page happily let you grant
 * "google_drive:*:read", because that is a different gate.
 *
 * The whole set goes in one PUT, matching the screen: a list of ticks
 * and one Save. Adding a service seeds READ permissions for it;
 * removing one revokes that service's permissions with it, so nothing
 * stays granted on a page that no longer lists it.
 */
export function setAgentServices(
  id: string,
  services: string[],
): Promise<AgentDetail> {
  return api<AgentDetail>(`/api/agents/${id}/services`, {
    method: "PUT",
    json: { services },
  });
}
