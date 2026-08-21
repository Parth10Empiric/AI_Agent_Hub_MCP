/**
 * What the user has left this hour (Phase 5.7).
 *
 * A limit nobody can see is a limit that arrives as a mystery failure.
 * This is what lets the UI show a budget SHRINKING rather than a
 * surprise refusal - "3 turns left" instead of "the agent stopped
 * working".
 *
 * The backend answers with peek(), not check(), so asking never costs
 * anything. Polling this is safe.
 */

import { api } from "./client";
import type { LimitsRead } from "../types";

export function getLimits(agentId?: string): Promise<LimitsRead> {
  const query = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";

  return api<LimitsRead>(`/api/limits${query}`);
}
