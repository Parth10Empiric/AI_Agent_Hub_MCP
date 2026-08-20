import { api } from "./client";
import type { components } from "./schema";

export type ExecutionListItem =
  components["schemas"]["ExecutionListItem"];
export type ExecutionPage = components["schemas"]["ExecutionPage"];
export type ExecutionStats = components["schemas"]["ExecutionStats"];

export interface ExecutionFilters {
  status?: "success" | "failed" | "denied";
  agent_id?: string;
  days?: number;
  limit?: number;
  cursor?: string;
}

export function listExecutions(
  filters: ExecutionFilters = {},
): Promise<ExecutionPage> {
  const params = new URLSearchParams();

  // Object.entries + a skip for empty values, rather than a chain of
  // ifs. Sending `status=` with no value would be rejected by the
  // backend's validation, so undefined must never become a parameter.
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }

  const query = params.toString();

  return api<ExecutionPage>(`/api/executions${query ? `?${query}` : ""}`);
}

export function getExecutionStats(days = 7): Promise<ExecutionStats> {
  return api<ExecutionStats>(`/api/executions/stats?days=${days}`);
}
