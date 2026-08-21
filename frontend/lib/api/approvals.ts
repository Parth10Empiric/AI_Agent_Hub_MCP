/**
 * Pending approvals (Phase 5.2).
 *
 * An approval is a tool call the agent has STOPPED on. The turn is
 * suspended on the server, waiting - it is not a notification about
 * something that already happened. Answering resumes it; letting it
 * expire denies it.
 *
 * That is why every call here matters more than a normal read: the
 * agent is holding still until one of them returns.
 */

import { api } from "./client";
import type { ApprovalPage, ApprovalRead } from "../types";

export function listApprovals(
  options: { status?: string | null; limit?: number } = {},
): Promise<ApprovalPage> {
  const params = new URLSearchParams();

  // "" means "every status" on the backend - the inbox default is
  // pending, and passing null asks for the full history instead.
  if (options.status !== undefined) {
    params.set("status", options.status ?? "");
  }

  if (options.limit) params.set("limit", String(options.limit));

  const query = params.toString();

  return api<ApprovalPage>(`/api/approvals${query ? `?${query}` : ""}`);
}

export function getApproval(id: string): Promise<ApprovalRead> {
  return api<ApprovalRead>(`/api/approvals/${id}`);
}

export function approveCall(id: string): Promise<ApprovalRead> {
  return api<ApprovalRead>(`/api/approvals/${id}/approve`, {
    method: "POST",
  });
}

export function denyCall(id: string): Promise<ApprovalRead> {
  return api<ApprovalRead>(`/api/approvals/${id}/deny`, { method: "POST" });
}
